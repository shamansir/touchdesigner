"""Just enough of TouchDesigner's object model to run hydra_compile outside TD.

Paths are deliberately opaque: `relativePath(o)` returns a token like `@12`,
and `resolve()` accepts exactly those tokens and nothing else. TD's own rules
for combining relative paths are not ours to rely on -- 0.2.0 appended
'/<child>' to a relative path and every uniform evaluated op(None)[0]. Here any
path built rather than asked for fails to resolve.
"""

import json
import os
import re
import types

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_registry = {}


def resolve(path):
    """The op a path token names, or None -- as TD's op() would return."""
    m = re.fullmatch(r'@(\d+)', path)
    return _registry.get(int(m.group(1))) if m else None


def load_module(filename, name):
    m = types.ModuleType(name)
    m.__file__ = os.path.join(HERE, filename)
    with open(m.__file__) as f:
        exec(compile(f.read(), filename, 'exec'), m.__dict__)
    return m


hc = load_module('hydra_compile.py', 'hydra_compile')


def load_specs():
    with open(os.path.join(HERE, 'hydra-functions.json')) as f:
        specs = {s['name']: s for s in json.load(f)}
    with open(os.path.join(HERE, 'hydra-extensions.json')) as f:
        for name, extra in json.load(f).items():
            if not name.startswith('_'):
                specs[name]['inputs'] = specs[name]['inputs'] + extra['inputs']
    return specs


def load_utils():
    with open(os.path.join(HERE, 'hydra-utils.glsl')) as f:
        parts = re.split(r'^// =====\s*(\w+)\s*=====\s*$', f.read(), flags=re.M)
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


SPECS = load_specs()
UTILS = load_utils()


class Connector:
    def __init__(self, owner, in_op=None):
        self.owner, self.inOP, self.connections = owner, in_op, []


class Conn:
    """One end of a wire, as a Connector's `.connections` lists it."""
    def __init__(self, owner, in_op=None):
        self.owner, self.inOP = owner, in_op


class Node:
    isCOMP = False

    def __init__(self, name, parent=None):
        self.id = len(_registry) + 1
        _registry[self.id] = self
        self.name = name
        self._parent = parent
        self.path = f'{parent.path}/{name}' if parent else f'/{name}'
        self.tags = set()
        self.children = {}
        self.inputConnectors = []
        self.outputConnectors = [Connector(self)]
        if parent:
            parent.children[name] = self

    def parent(self):
        return self._parent

    def op(self, name):
        return self.children.get(name)

    def relativePath(self, o):
        return f'@{o.id}'


class Par:
    def __init__(self, name, value=None):
        self.name, self.value = name, value

    def eval(self):
        return self.value


class HydraComp(Node):
    """A built hydra component: In TOPs, In CHOPs, glsl, stored spec, pars."""
    isCOMP = True
    viewer = False

    def __init__(self, fn, name=None, parent=None, mode='compiled'):
        super().__init__(name or f'hydra_{fn.lower()}', parent)
        spec = SPECS[fn]
        body = spec['glsl']
        self.tags = {hc.HYDRA_TAG, f'hydra:fn:{fn}', f"hydra:class:{spec['type']}"}
        self.storage = {hc.SPEC_KEY: {
            'name': fn, 'type': spec['type'], 'inputs': spec['inputs'], 'glsl': body,
            'utils': {u: UTILS[u] for u in hc.UTILS if re.search(rf'\b{u}\b', body)},
            'uses': hc.uses_of(body),
            'alias': hc.TEXTURE_ALIAS.get(fn),
        }}
        for label in hc.inputs_for(spec):
            Node(label, self)
            self.inputConnectors.append(Connector(self, self.children[label]))
        for i in spec['inputs']:
            if i['type'] == 'float':
                Node(i['name'], self)                     # its In CHOP
        Node('glsl', self)
        pars = [Par('Mode', mode)]
        if 'time' in self.storage[hc.SPEC_KEY]['uses']:
            pars.append(Par('Time', 0.0))
        if spec['type'] == 'src' and fn not in hc.TEXTURE_INPUT:
            pars += [Par('Resolution1', 1280), Par('Resolution2', 720)]
        for i in spec['inputs']:
            if i['type'].startswith('vec'):
                pars += [Par(f"{hc.par_name(i['name'])}{k}", 1.0)
                         for k in range(1, int(i['type'][3:]) + 1)]
        self.customPars = pars
        self.par = types.SimpleNamespace(**{p.name: p for p in pars})

    def fetch(self, key, default=None, search=True):
        return self.storage.get(key, default)

    def store(self, key, value):
        self.storage[key] = value

    def pars(self, pattern):
        prefix = pattern.rstrip('*')
        return [p for p in self.customPars if p.name.startswith(prefix)]


def wire(src, dst, label):
    """src's output into dst's input whose In TOP is `label`."""
    ic = next(c for c in dst.inputConnectors if c.inOP.name == label)
    ic.connections = [Conn(src)]
    src.outputConnectors[0].connections.append(Conn(dst, ic.inOP))


def network(name='sketch'):
    return Node(name)
