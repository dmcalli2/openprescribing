from numbers import Number
import argparse
import json
import os

from gcutils.bigquery import Client
from mock import patch
from mock import MagicMock

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase

from frontend.bq_schemas import CCG_SCHEMA, PRACTICE_SCHEMA, PRESCRIBING_SCHEMA
from frontend.management.commands.import_measures import Command
from frontend.management.commands.import_measures import parse_measures
from frontend.models import Measure
from frontend.models import MeasureValue, MeasureGlobal, Chemical
from frontend.models import PCT
from frontend.models import Practice


def isclose(a, b, rel_tol=0.001, abs_tol=0.0):
    if isinstance(a, Number) and isinstance(b, Number):
        return abs(a-b) <= max(rel_tol * max(abs(a), abs(b)), abs_tol)
    else:
        return a == b


def test_measures():
    fpath = settings.INSTALL_ROOT
    fname = os.path.join(
        fpath, ('openprescribing/frontend/tests/fixtures/'
                'cerazette_measure.json'))
    json_data = json.load(open(fname))
    return {
        'cerazette': json_data
    }


@patch('frontend.management.commands.import_measures.parse_measures',
       new=MagicMock(return_value=test_measures()))
class ArgumentTestCase(TestCase):
    def test_months_parsed_from_hscic_filename(self):
        opts = [
            '--month_from_prescribing_filename',
            'data/prescribing/2016_03/T201603PDPI BNFT_formatted.CSV'
        ]
        parser = argparse.ArgumentParser()
        cmd = Command()
        parser = cmd.create_parser("import_measures", "")
        options = parser.parse_args(opts)
        result = cmd.parse_options(options.__dict__)
        self.assertEqual(result['start_date'], '2016-03-01')
        self.assertEqual(result['end_date'], '2016-03-01')


@patch('frontend.management.commands.import_measures.parse_measures',
       new=MagicMock(return_value=test_measures()))
class UnitTests(TestCase):
    """Unit tests with mocked bigquery. Many of the functional
    tests could be moved hree.

    """
    fixtures = ['measures']

    @patch('django.db.connection')
    def test_reconstructor_not_called_when_measures_specified(self, conn):
        from frontend.management.commands.import_measures \
            import conditional_constraint_and_index_reconstructor
        with conditional_constraint_and_index_reconstructor(
                {'measure': 'thingy'}):
            pass
        execute = conn.cursor.return_value.__enter__.return_value.execute
        execute.assert_not_called()

    @patch('frontend.management.commands.import_measures.connection')
    def test_reconstructor_called_when_no_measures_specified(self, conn):
        from frontend.management.commands.import_measures \
            import conditional_constraint_and_index_reconstructor
        with conditional_constraint_and_index_reconstructor({}):
            pass
        calls = conn.cursor.return_value.__enter__\
                    .return_value.execute.mock_calls
        self.assertGreater(calls, 0)


class BigqueryFunctionalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.env = patch.dict(
            'os.environ', {'DB_NAME': 'test_' + os.environ['DB_NAME']})
        with cls.env:
            cls._createData()

    def test_import_measurevalue_by_practice_with_different_payments(self):
        month = '2015-10-01'
        measure_id = 'cerazette'
        args = []
        opts = {
            'month': month,
            'measure': measure_id,
            'v': 3
        }
        with patch('frontend.management.commands.import_measures'
                   '.parse_measures',
                   new=MagicMock(return_value=test_measures())):
            call_command('import_measures', *args, **opts)

        m = Measure.objects.get(id='cerazette')
        month = '2015-10-01'
        p = Practice.objects.get(code='C83051')
        mv = MeasureValue.objects.get(measure=m, month=month, practice=p)
        self.assertEqual("%.2f" % mv.cost_savings['50'], '0.00')

        p = Practice.objects.get(code='C83019')
        mv = MeasureValue.objects.get(measure=m, month=month, practice=p)
        self.assertEqual("%.2f" % mv.cost_savings['50'], '325.58')

        p = Practice.objects.get(code='A86030')
        mv = MeasureValue.objects.get(measure=m, month=month, practice=p)
        self.assertEqual("%.2f" % mv.cost_savings['50'], '-42.86')

    def test_measure_is_created(self):
        m = Measure.objects.get(id='cerazette')
        self.assertEqual(m.name, 'Cerazette vs. Desogestrel')
        self.assertEqual(m.description[:10], 'Total quan')
        self.assertEqual(m.why_it_matters[:10], 'This is th')
        self.assertEqual(m.low_is_good, True)

    def test_practice_general(self):
        month = '2015-09-01'
        measure = Measure.objects.get(id='cerazette')
        expected = {
            'C84001': {
                'numerator': 1000,
                'denominator': 11000,
                'calc_value': 0.0909,
                'num_items': 10,
                'denom_items': 110,
                'num_cost': 1000.0,
                'denom_cost': 2000,
                'denom_quantity': 11000,
                'percentile': 33.33,
                'pct.code': '02Q',
                'cost_savings': {
                    '10': 485.58,
                    '20': 167.44,
                    '50': -264.71,
                    '70': -3126.00,
                    '90': -7218.00
                }
            }
        }
        self._assertExpectedMeasureValue(measure, month, expected)

    def test_practice_with_no_prescribing(self):
        month = '2015-09-01'
        measure = Measure.objects.get(id='cerazette')
        expected = {
            'B82010': {
                'numerator': 0,
                'denominator': 0,
                'calc_value': None,
                'num_items': None,
                'denom_items': None,
                'num_cost': None,
                'denom_cost': None,
                'denom_quantity': None,
                'percentile': None,
                'cost_savings': {
                    '10': 0,
                    '90': 0
                }
            }
        }
        self._assertExpectedMeasureValue(measure, month, expected)

    def test_practice_with_positive_cost_savings(self):
        month = '2015-09-01'
        measure = Measure.objects.get(id='cerazette')
        expected = {
            'A85017': {
                'numerator': 1000,
                'denominator': 1000,
                'calc_value': 1,
                'percentile': 100,
                'cost_savings': {
                    '10': 862.33,
                    '90': 162.00
                }
            }
        }
        self._assertExpectedMeasureValue(measure, month, expected)

    def test_practice_with_negative_cost_savings(self):
        month = '2015-09-01'
        measure = Measure.objects.get(id='cerazette')
        expected = {
            'A86030': {
                'numerator': 0,
                'denominator': 1000,
                'calc_value': 0,
                'percentile': 0,
                'cost_savings': {
                    '10': -37.67,
                    '90': -738.00
                }
            }
        }
        self._assertExpectedMeasureValue(measure, month, expected)

    def test_practice_in_top_quartile(self):
        month = '2015-09-01'
        measure = Measure.objects.get(id='cerazette')
        expected = {
            'C83051': {
                'numerator': 1500,
                'denominator': 21500,
                'calc_value': 0.0698,
                'percentile': 16.67,
                'cost_savings': {
                    '10': 540.00,
                    '90': -14517.00
                }
            }
        }
        self._assertExpectedMeasureValue(measure, month, expected)

    def test_practice_at_median(self):
        month = '2015-09-01'
        measure = Measure.objects.get(id='cerazette')
        expected = {
            'C83019': {
                'numerator': 2000,
                'denominator': 17000,
                'calc_value': 0.1176,
                'percentile': 50,
                'cost_savings': {
                    '10': 1159.53,
                    '90': -10746.00
                }
            }
        }
        self._assertExpectedMeasureValue(measure, month, expected)

    def test_ccg_at_0th_centile(self):
        month = '2015-09-01'
        measure = Measure.objects.get(id='cerazette')
        expected = {
            '04D': {
                'numerator': 1500,
                'denominator': 21500,
                'calc_value': 0.0698,
                'percentile': 0
            }
        }
        self._assertExpectedMeasureValue(measure, month, expected)

    def test_ccg_at_100th_centile(self):
        month = '2015-09-01'
        measure = Measure.objects.get(id='cerazette')
        expected = {
            '02Q': {
                'numerator': 82000,
                'denominator': 143000,
                'calc_value': 0.5734,
                'percentile': 100,
                'cost_savings': {
                    '10': 63588.51,
                    '30': 61123.67,
                    '50': 58658.82,
                    '80': 23463.53,
                    '90': 11731.76
                }
            }
        }
        self._assertExpectedMeasureValue(measure, month, expected)

    def test_ccg_at_50th_centile(self):
        month = '2015-09-01'
        measure = Measure.objects.get(id='cerazette')
        expected = {
            '03T': {
                'numerator': 2000,
                'denominator': 17000,
                'calc_value': 0.1176,
                'percentile': 50
            }
        }
        self._assertExpectedMeasureValue(measure, month, expected)

    def test_import_measureglobal(self):  # failing
        month = '2015-09-01'
        measure = Measure.objects.get(id='cerazette')
        expected = {
            '_global_': {
                'numerator': 85500,
                'denominator': 181500,
                'num_items': 855,
                'denom_items': 1815,
                'num_cost': 85500,
                'denom_cost': 95100,
                'num_quantity': 85500,
                'denom_quantity': 181500,
                'calc_value': 0.4711,
                'percentiles': {
                    'practice': {
                        '10': 0.0419,
                        '20': 0.0740,
                        '50': 0.1176,
                        '70': 0.4067,
                        '90': 0.8200
                    },
                    'ccg': {
                        '10': 0.0793,
                        '30': 0.0985,
                        '50': 0.1176,
                        '80': 0.3911,
                        '90': 0.4823
                    },
                },
                'cost_savings': {
                    'practice': {
                        '10': 70149.77,
                        '20': 65011.21,
                        '50': 59029.41,
                        '70': 26934.00,
                        '90': 162.00
                    },
                    'ccg': {
                        '10': 64174.56,  # this one got 62795.62
                        '30': 61416.69,
                        '50': 58658.82,
                        '80': 23463.53,
                        '90': 11731.76
                    },
                },
            }
        }
        self._assertExpectedMeasureValue(measure, month, expected)

    @classmethod
    def _createData(cls):
        bassetlaw = PCT.objects.create(code='02Q', org_type='CCG')
        lincs_west = PCT.objects.create(code='04D', org_type='CCG')
        lincs_east = PCT.objects.create(code='03T', org_type='CCG',
                                        open_date='2013-04-01',
                                        close_date='2015-01-01')
        Chemical.objects.create(bnf_code='0703021Q0',
                                chem_name='Desogestrel')
        Chemical.objects.create(bnf_code='0408010A0',
                                chem_name='Levetiracetam')
        Practice.objects.create(code='C84001', ccg=bassetlaw,
                                name='LARWOOD SURGERY', setting=4)
        Practice.objects.create(code='C84024', ccg=bassetlaw,
                                name='NEWGATE MEDICAL GROUP', setting=4)
        Practice.objects.create(code='B82005', ccg=bassetlaw,
                                name='PRIORY MEDICAL GROUP', setting=4,
                                open_date='2015-01-01')
        Practice.objects.create(code='B82010', ccg=bassetlaw,
                                name='RIPON SPA SURGERY', setting=4)
        Practice.objects.create(code='A85017', ccg=bassetlaw,
                                name='BEWICK ROAD SURGERY', setting=4)
        Practice.objects.create(code='A86030', ccg=bassetlaw,
                                name='BETTS AVENUE MEDICAL GROUP', setting=4)
        Practice.objects.create(code='C83051', ccg=lincs_west,
                                name='ABBEY MEDICAL PRACTICE', setting=4)
        Practice.objects.create(code='C83019', ccg=lincs_east,
                                name='BEACON MEDICAL PRACTICE', setting=4)
        # Ensure we only include open practices in our calculations.
        Practice.objects.create(code='B82008', ccg=bassetlaw,
                                name='NORTH SURGERY', setting=4,
                                open_date='2010-04-01',
                                close_date='2012-01-01')
        # Ensure we only include standard practices in our calculations.
        Practice.objects.create(code='Y00581', ccg=bassetlaw,
                                name='BASSETLAW DRUG & ALCOHOL SERVICE',
                                setting=1)

        args = []
        if 'SKIP_BQ_LOAD' not in os.environ:
            fixtures_path = os.path.join(
                'frontend', 'tests', 'fixtures', 'commands')

            prescribing_fixture_path = os.path.join(
                fixtures_path,
                'prescribing_bigquery_fixture.csv'
            )
            # TODO Make this a table with a view (see
            # generate_presentation_replacements), and put it in the correct
            # dataset ('hscic', not 'measures').
            client = Client('measures')
            table = client.get_or_create_table(
                'normalised_prescribing_legacy',
                PRESCRIBING_SCHEMA
            )
            table.insert_rows_from_csv(prescribing_fixture_path)

            practices_fixture_path = os.path.join(
                fixtures_path,
                'practices.csv'
            )
            client = Client('hscic')
            table = client.get_or_create_table('practices', PRACTICE_SCHEMA)
            columns = [field.name for field in PRACTICE_SCHEMA]
            table.insert_rows_from_csv(practices_fixture_path)

            ccgs_fixture_path = os.path.join(
                fixtures_path,
                'ccgs.csv'
            )
            table = client.get_or_create_table('ccgs', CCG_SCHEMA)
            table.insert_rows_from_csv(ccgs_fixture_path)

        month = '2015-09-01'
        measure_id = 'cerazette'
        args = []
        opts = {
            'month': month,
            'measure': measure_id,
            'v': 3
        }
        with patch('frontend.management.commands.import_measures'
                   '.parse_measures',
                   new=MagicMock(return_value=test_measures())):
            call_command('import_measures', *args, **opts)

    def _walk(self, mv, data):
        for k, v in data.items():
            if '.' in k:
                relation, attr = k.split('.')
                model = getattr(mv, relation)
                actual = getattr(model, attr)
                expected = v
                identifier = k
            elif isinstance(v, dict):
                field = getattr(mv, k)
                for k2, v2 in v.items():
                    actual = field.get(k2)
                    expected = v2
                    identifier = "%s[%s]" % (k, k2)
                    if isinstance(v2, dict):
                        # BQ returns strings:
                        try:
                            actual = json.loads(actual)
                        except TypeError:
                            # Already decoded it
                            pass
                        for k3, v3 in v2.items():
                            yield actual.get(k3), v3, "[%s]" % k3
                    else:
                        yield actual, expected, identifier

            else:
                actual = getattr(mv, k)
                expected = v
                identifier = k
                yield actual, expected, identifier

    def _assertExpectedMeasureValue(self, measure, month, expected):
        for entity_id, data in expected.items():
            if entity_id == '_global_':
                mv = MeasureGlobal.objects.get(
                    measure=measure, month=month)
            elif len(entity_id) == 3:
                ccg = PCT.objects.get(code=entity_id)
                mv = MeasureValue.objects.get(
                    measure=measure, month=month, pct=ccg, practice=None)
            else:

                p = Practice.objects.get(code=entity_id)
                mv = MeasureValue.objects.get(
                    measure=measure, month=month, practice=p)
            for actual, expected, identifier in self._walk(mv, data):
                if isinstance(expected, Number):
                    self.assert_(
                        isclose(actual, expected),
                        "got %s for %s, expected ~ %s" % (
                            actual, identifier, expected))
                else:
                    self.assert_(
                        actual == expected,
                        "got %s for %s, expected %s" % (
                            actual, identifier, expected))


class TestParseMeasures(TestCase):
    def test_parse_measures(self):
        measures = parse_measures()
        lpzomnibus_ix = list(measures).index('lpzomnibus')
        lptrimipramine_ix = list(measures).index('lptrimipramine')

        self.assertTrue(lptrimipramine_ix < lpzomnibus_ix)
