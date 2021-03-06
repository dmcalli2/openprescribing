# coding=utf8

import calendar
import datetime
import logging
import re

import bs4
import requests

from django.core.management import BaseCommand

from dmd.models import NCSOConcession, DMDVmpp
from gcutils.bigquery import Client
from openprescribing.slack import notify_slack

logger = logging.getLogger(__file__)


class Command(BaseCommand):
    def handle(self, *args, **kwargs):
        self.vmpps = DMDVmpp.objects.values('nm', 'vppid')
        self.counter = {
            'new-and-matched': 0,
            'new-and-unmatched': 0,
            'changed': 0,
            'unchanged': 0,
        }
        self.import_from_archive()
        self.import_from_current()

        logger.info('New and matched: %s', self.counter['new-and-matched'])
        logger.info('New and unmatched: %s', self.counter['new-and-unmatched'])
        logger.info('Changed: %s', self.counter['changed'])
        logger.info('Unchanged: %s', self.counter['unchanged'])

        Client('dmd').upload_model(NCSOConcession)

        msg = '\n'.join([
            'Imported NCSO concessions',
            'New and matched: %s' % self.counter['new-and-matched'],
            'New and unmatched: %s' % self.counter['new-and-unmatched'],
            'Changed: %s' % self.counter['changed'],
            'Unchanged: %s' % self.counter['unchanged'],
        ])
        notify_slack(msg)

    def import_from_archive(self):
        logger.info('import_from_archive')

        doc = self.download_archive()
        for h2 in doc.find_all('h2', class_='trigger'):
            self.import_from_html(h2)

    def download_archive(self):
        url = 'http://psnc.org.uk/dispensing-supply/supply-chain/generic-shortages/ncso-archive/'
        rsp = requests.get(url)
        return bs4.BeautifulSoup(rsp.content, 'html.parser')

    def import_from_current(self):
        logger.info('import_from_current')

        doc = self.download_current()
        h1s = doc.find_all('h1', string=re.compile('\w+ \d{4}'))
        assert len(h1s) == 1
        self.import_from_html(h1s[0])

    def download_current(self):
        url = 'http://psnc.org.uk/dispensing-supply/supply-chain/generic-shortages/'
        rsp = requests.get(url)
        return bs4.BeautifulSoup(rsp.content, 'html.parser')

    def import_from_html(self, heading):
        month_name, year = heading.text.strip().split()
        month_names = list(calendar.month_name)
        month = month_names.index(month_name)

        date = datetime.date(int(year), month, 1)

        if date < datetime.date(2014, 8, 1):
            return

        table = heading.find_next('table')
        trs = table.find_all('tr')
        records = [[td.text.strip() for td in tr.find_all('td')] for tr in trs]

        # Make sure the first row contains expected headers.
        # Unfortunately, the header names are not consistent.
        assert 'drug' in records[0][0].lower()
        assert 'pack' in records[0][1].lower()
        assert 'price' in records[0][2].lower()

        for record in records[1:]:
            drug, pack_size, price_concession = record
            drug = drug.replace('(new)', '').strip()
            match = re.match(u'£(\d+)\.(\d\d)', price_concession)
            price_concession_pence = 100 * int(match.groups()[0]) \
                + int(match.groups()[1])
            self.import_record(date, drug, pack_size, price_concession_pence)

        if NCSOConcession.objects.filter(date=date).count() >= len(records):
            # If there are more records in the database than we have imported,
            # then there was previously a record in the source that is no
            # longer present.  I have seen this once, with a record whose
            # spelling was corrected.  If we see this frequently, we should
            # automate dealing with it; for now we can deal with it manually.
            msg = 'NCSO concession(s) removed from source for {}'.format(date)
            notify_slack(msg)

    def import_record(self, date, drug, pack_size, price_concession_pence):
        concession, created = NCSOConcession.objects.get_or_create(
            date=date,
            drug=drug,
            pack_size=pack_size,
            defaults={'price_concession_pence': price_concession_pence}
        )

        if created:
            logger.info('Created new NCSOConcession for %s %s',
                        drug, pack_size)
            concession.price_concession_pence = price_concession_pence
            matching_vmpp_id = self.get_matching_vmpp_id(concession)
            if matching_vmpp_id is not None:
                logger.info('Found matching VMPP: %s', matching_vmpp_id)
                concession.vmpp_id = matching_vmpp_id
                status = 'new-and-matched'
            else:
                logger.info('Found no matching VMPP')
                status = 'new-and-unmatched'

            concession.save()

        elif concession.price_concession_pence != price_concession_pence:
            logger.info('Price has changed for %s %s', drug, pack_size)
            logger.info('Was: %s', concession.price_concession_pence)
            logger.info('Now: %s', price_concession_pence)
            concession.price_concession_pence = price_concession_pence
            concession.save()
            status = 'changed'

        else:
            status = 'unchanged'

        self.counter[status] += 1

    def get_matching_vmpp_id(self, concession):
        previous_concession = NCSOConcession.objects.filter(
            drug=concession.drug,
            pack_size=concession.pack_size,
        ).exclude(
            date=concession.date
        ).first()

        if previous_concession is not None:
            logger.info('Found previous matching concession')
            return previous_concession.vmpp_id

        ncso_name_raw = u'{} {}'.format(concession.drug, concession.pack_size)
        ncso_name = self.regularise_ncso_name(ncso_name_raw)

        for vmpp in self.vmpps:
            vpmm_name = re.sub(' */ *', '/', vmpp['nm'].lower())

            if vpmm_name == ncso_name or vpmm_name.startswith(ncso_name + ' '):
                logger.info('Found match')
                return vmpp['vppid']

        logger.info('No match found')
        return None

    def regularise_ncso_name(self, name):
        # Some NCSO records have non-breaking spaces
        name = name.replace(u'\xa0', '')

        # Some NCSO records have multiple spaces
        name = re.sub(' +', ' ', name)

        # dm+d uses "microgram" or "micrograms", usually with these rules
        name = name.replace('mcg ', 'microgram ')
        name = name.replace('mcg/', 'micrograms/')

        # dm+d uses "microgram" rather than "0.X.mg"
        name = name.replace('0.5mg', '500microgram')
        name = name.replace('0.25mg', '250microgram')

        # dm+d uses "square cm"
        name = name.replace('sq cm', 'square cm')

        # dm+d records measured in mg/ml have a space before the final "ml"
        # eg: Abacavir 20mg/ml oral solution sugar free 240 ml
        name = re.sub(r'(\d)ml$', r'\1 ml', name)

        # dm+d records have "gram$" not "g$"
        # eg: Estriol 0.01% cream 80 gram
        name = re.sub(r'(\d)g$', r'\1 gram', name)

        # Misc. commont replacements
        name = name.replace('Oral Susp SF', 'oral suspension sugar free')
        name = name.replace('gastro- resistant', 'gastro-resistant')
        name = name.replace('/ml', '/1ml')

        # Lowercase
        name = name.lower()

        # Remove spaces around slashes
        name = re.sub(' */ *', '/', name)

        return name
