"""hydra_compile code generation, against a mock graph.

    python3 -m unittest discover assets/scripts/hydra/tests
"""

import re
import unittest

from td_mock import SPECS, UTILS, Conn, HydraComp, Node, hc, network, resolve, wire


def generate(root, inline=True):
    chain = hc.Chain(root, inline)
    return chain, chain.shader(chain.gen(root, 'st'))


def main_of(text):
    return text[text.index('void main'):]


def expressions(chain, root, inline=True):
    out = [e for _, _, exprs in chain.uniforms for e in exprs]
    res = hc.resolution(root, inline)
    return out + list(res or ())


class ChainShape(unittest.TestCase):

    def test_coord_ops_run_in_reverse_before_the_source(self):
        net = network()
        o, r, s = (HydraComp(f, parent=net) for f in ('osc', 'rotate', 'scale'))
        wire(o, r, 'source')
        wire(r, s, 'source')
        _, text = generate(s)
        body = main_of(text)
        # hydra: osc(rotate(scale(st)))
        self.assertLess(body.index('scale_0(st'), body.index('rotate_1(uv0'))
        self.assertLess(body.index('rotate_1('), body.index('osc_2(uv1'))

    def test_chained_modulates_grow_linearly(self):
        net = network()
        prev = HydraComp('osc', parent=net)
        k = 10
        for i in range(k):
            n = HydraComp('noise', f'n{i}', net)
            m = HydraComp('modulate', f'm{i}', net)
            wire(prev, m, 'source')
            wire(n, m, 'modulator')
            prev = m
        chain, _ = generate(prev)
        # per modulate: the modulator, the coordinate; plus the source
        self.assertEqual(len(chain.body), 2 * k + 1)

    def test_shared_input_at_the_same_coordinate_is_evaluated_once(self):
        net = network()
        o, add = HydraComp('osc', parent=net), HydraComp('add', parent=net)
        wire(o, add, 'source')
        wire(o, add, 'with')
        chain, text = generate(add)
        self.assertEqual(main_of(text).count('osc_1('), 1)
        self.assertEqual(len(chain.instances), 2)

    def test_modulator_is_evaluated_at_the_incoming_coordinate(self):
        net = network()
        o, n = HydraComp('osc', parent=net), HydraComp('noise', parent=net)
        r, m = HydraComp('rotate', parent=net), HydraComp('modulate', parent=net)
        wire(o, m, 'source')
        wire(n, m, 'modulator')
        wire(m, r, 'source')
        _, text = generate(r)
        body = main_of(text)
        # rotate runs first; the noise is sampled at rotate's output, osc at
        # the modulated coordinate
        self.assertRegex(body, r'noise_\d\(uv0,')
        self.assertRegex(body, r'modulate_\d\(uv0, c\d+,')


class Boundaries(unittest.TestCase):

    def test_texture_into_this_component_is_its_own_in_top(self):
        net = network()
        o, d = HydraComp('osc', parent=net), HydraComp('diff', parent=net)
        fb = Node('feedback1', net)
        wire(o, d, 'source')
        wire(fb, d, 'with')
        chain, text = generate(d)
        self.assertEqual([t.path for _, t in chain.boundaries], [d.op('with').path])
        self.assertIn('#define HYDRA_INPUT_0 sTD2DInputs[0]', text)

    def test_texture_further_up_is_that_components_in_top(self):
        net = network()
        o, d, p = (HydraComp(f, parent=net) for f in ('osc', 'diff', 'posterize'))
        wire(o, d, 'source')
        wire(Node('feedback1', net), d, 'with')
        wire(d, p, 'source')
        chain, _ = generate(p)
        self.assertEqual([t.path for _, t in chain.boundaries], [d.op('with').path])

    def test_image_mode_upstream_is_read_as_a_texture(self):
        net = network()
        o = HydraComp('osc', parent=net)
        img = HydraComp('rotate', parent=net, mode='image')
        p = HydraComp('posterize', parent=net)
        wire(o, img, 'source')
        wire(img, p, 'source')
        chain, text = generate(p)
        self.assertEqual(len(chain.instances), 1)
        self.assertIn('texture(HYDRA_INPUT_0, fract(st))', text)
        self.assertTrue(hc.is_output(o))    # an Image component reads its texture

    def test_image_mode_inlines_nothing(self):
        net = network()
        o, r = HydraComp('osc', parent=net), HydraComp('rotate', parent=net)
        wire(o, r, 'source')
        chain, text = generate(r, inline=False)
        self.assertEqual(len(chain.instances), 1)
        self.assertIn('texture(HYDRA_INPUT_0, fract(uv0))', text)

    def test_src_reads_its_texture_never_inlines_it(self):
        net = network()
        o, s = HydraComp('osc', parent=net), HydraComp('src', parent=net)
        wire(o, s, 'source')
        chain, text = generate(s)
        self.assertEqual(len(chain.instances), 1)
        self.assertIn('#define tex HYDRA_INPUT_0', text)
        self.assertTrue(hc.is_output(o))

    def test_unwired_input_is_transparent_black(self):
        net = network()
        _, text = generate(HydraComp('posterize', parent=net))
        self.assertIn('posterize_0(vec4(0.0),', text)


class Outputs(unittest.TestCase):

    def test_end_of_chain_and_texture_readers_are_outputs(self):
        net = network()
        o, r = HydraComp('osc', parent=net), HydraComp('rotate', parent=net)
        wire(o, r, 'source')
        self.assertFalse(hc.is_output(o))     # r inlines it
        self.assertTrue(hc.is_output(r))      # nothing reads it: end of chain
        r.outputConnectors[0].connections.append(Conn(Node('null1', net)))
        self.assertTrue(hc.is_output(r))      # a plain TOP reads it


class Paths(unittest.TestCase):

    def test_every_expression_path_is_one_relativePath_asked_for(self):
        net = network()
        o, n, m = (HydraComp(f, parent=net) for f in ('osc', 'noise', 'modulate'))
        s, d = HydraComp('sum', parent=net), HydraComp('diff', parent=net)
        wire(o, m, 'source')
        wire(n, m, 'modulator')
        wire(m, s, 'source')
        wire(s, d, 'source')
        wire(Node('movie', net), d, 'with')
        chain, _ = generate(d)
        paths = [p for e in expressions(chain, d)
                 for p in re.findall(r"op\('([^']*)'\)", e)]
        self.assertTrue(paths)
        for p in paths:
            self.assertIsNotNone(resolve(p), f'op({p!r}) resolves to nothing')


class Glsl(unittest.TestCase):

    def test_every_function_generates_alone_and_compiled(self):
        for fn in SPECS:
            for inline in (True, False):
                with self.subTest(fn=fn, inline=inline):
                    net = network()
                    comp = HydraComp(fn, parent=net)
                    for label in hc.inputs_for(SPECS[fn]):
                        wire(Node(f'{label}_tex', net), comp, label)
                    chain, text = generate(comp, inline)
                    self.assertEqual(text.count('{'), text.count('}'))
                    for _, name, _ in chain.uniforms:
                        self.assertGreater(len(re.findall(rf'\b{name}\b', text)), 1,
                                           f'{name} declared, never used')
                    defs = re.findall(r'^(?:vec[234]|float) (\w+)\(', text, re.M)
                    self.assertEqual(len(defs), len(set(defs)), 'duplicate function')

    def test_macro_names_occur_in_no_hydra_body_or_utility(self):
        # a #define rewrites every later occurrence of its name -- `s0` once
        # turned a local in _noise into `vec4 sTD2DInputs[0] = ...`
        net = network()
        n, d = HydraComp('noise', parent=net), HydraComp('diff', parent=net)
        wire(n, d, 'source')
        wire(Node('fb', net), d, 'with')
        _, text = generate(d)
        scoped = {'time', 'tex', 'prevBuffer', 'texture2D'}   # #undef'd around bodies
        names = set(re.findall(r'#define (\w+)', text)) - scoped
        corpus = ' '.join([s['glsl'] for s in SPECS.values()] + list(UTILS.values()))
        for name in names:
            self.assertIsNone(re.search(rf'\b{name}\b', corpus), name)

    def test_sum_keeps_only_its_vec4_function(self):
        net = network()
        s1, s2 = HydraComp('sum', 'a', net), HydraComp('sum', 'b', net)
        wire(s1, s2, 'source')
        _, text = generate(s2)
        self.assertNotIn('float sum(vec2', text)

    def test_extension_amount_wraps_the_call(self):
        net = network()
        _, text = generate(HydraComp('layer', parent=net))
        self.assertIn('return mix(_c0, layer_0(_c0, _c1), amount);', text)
        self.assertIn('layer_0_x(', main_of(text))


class Resolution(unittest.TestCase):

    def test_compiled_chain_takes_its_root_sources_canvas(self):
        net = network()
        o, r = HydraComp('osc', parent=net), HydraComp('rotate', parent=net)
        wire(o, r, 'source')
        res = hc.resolution(r, True)
        self.assertEqual(res, (f"op('@{o.id}').par.Resolution1",
                               f"op('@{o.id}').par.Resolution2"))

    def test_image_mode_follows_its_input(self):
        net = network()
        o, r = HydraComp('osc', parent=net), HydraComp('rotate', parent=net)
        wire(o, r, 'source')
        self.assertIsNone(hc.resolution(r, False))

    def test_a_source_sets_its_own(self):
        self.assertIsNone(hc.resolution(HydraComp('osc', parent=network()), True))


if __name__ == '__main__':
    unittest.main()
