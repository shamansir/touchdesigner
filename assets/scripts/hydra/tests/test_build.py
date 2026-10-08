"""build_hydra's TD-free parts: shared facts, specs, stored spec, shims.

    python3 -m unittest discover assets/scripts/hydra/tests
"""

import unittest

from td_mock import HydraComp, hc, load_module, network

b = load_module('build_hydra.py', 'build_hydra')


class SharedFacts(unittest.TestCase):

    def test_the_builder_takes_its_facts_from_the_compiler(self):
        for name in ('HYDRA_TAG', 'SPEC_KEY', 'CLASS_INPUTS', 'TEXTURE_ALIAS', 'UTILS'):
            self.assertEqual(getattr(b, name), getattr(b.core, name), name)
        self.assertEqual(b.par_name('repeatX'), 'Repeatx')
        self.assertEqual(b.inputs_for({'name': 'prev', 'type': 'src'}), ['source'])
        self.assertEqual(b.inputs_for({'name': 'modulate', 'type': 'combineCoord'}),
                         ['source', 'modulator'])


class Specs(unittest.TestCase):

    def test_extensions_merge_over_hydras_table(self):
        specs = {s['name']: s for s in b.load_specs()}
        self.assertEqual(len(specs), 52)
        amount = [i for i in specs['layer']['inputs'] if i['name'] == 'amount']
        self.assertEqual(amount[0].get('extension'), 'blend_amount')

    def test_utils_split_on_their_markers(self):
        self.assertEqual(set(b.load_utils()), set(hc.UTILS))

    def test_stored_spec_carries_what_the_compiler_reads(self):
        specs = {s['name']: s for s in b.load_specs()}
        utils = b.load_utils()
        for fn, expect in (('noise', {'utils': ['_noise'], 'uses': ['time']}),
                           ('modulateHue', {'utils': [], 'uses': ['resolution']}),
                           ('src', {'utils': [], 'uses': [], 'alias': 'tex'})):
            comp = HydraComp(fn, parent=network())
            comp.storage.clear()
            b.store_spec(comp, specs[fn], utils)
            stored = comp.storage[hc.SPEC_KEY]
            self.assertEqual(sorted(stored['utils']), expect['utils'], fn)
            self.assertEqual(stored['uses'], expect['uses'], fn)
            self.assertEqual(stored['alias'], expect.get('alias'), fn)


class Identification(unittest.TestCase):

    def test_stored_spec_wins_over_the_name(self):
        by_name = {s['name'].lower(): s for s in b.load_specs()}
        comp = HydraComp('osc', name='hydra_rotate_but_really_osc', parent=network())
        self.assertEqual(b.spec_name(comp, by_name), 'osc')

    def test_tag_without_a_stored_spec(self):
        by_name = {s['name'].lower(): s for s in b.load_specs()}
        comp = HydraComp('scrollX', name='hydra_scrollx_rangga3', parent=network())
        comp.storage.clear()
        self.assertEqual(b.spec_name(comp, by_name), 'scrollx')


class Shims(unittest.TestCase):

    def test_every_shim_calls_a_function_the_compiler_has(self):
        for text in (b.CHAIN_EXEC, b.PAR_EXEC):
            calls = [line.split('.module.')[1].split('(')[0]
                     for line in text.splitlines() if '.module.' in line]
            self.assertTrue(calls)
            for name in calls:
                self.assertTrue(callable(getattr(hc, name, None)), name)
            compile(text, 'shim', 'exec')            # valid Python


class Names(unittest.TestCase):

    def test_expand_names(self):
        self.assertEqual(b.expand_names('osc*2 rotate*3 scale'),
                         ['osc', 'osc', 'rotate', 'rotate', 'rotate', 'scale'])
        self.assertEqual(b.expand_names(['osc*2', 'noise']), ['osc', 'osc', 'noise'])


if __name__ == '__main__':
    unittest.main()
