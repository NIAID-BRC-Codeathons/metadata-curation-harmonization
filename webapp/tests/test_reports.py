import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode
from unittest.mock import patch
from app import create_app
import database


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'test.sqlite3'
        self.app = create_app({'TESTING':True,'DATABASE':str(self.path),'SECRET_KEY':'test'})
        self.client = self.app.test_client()
        self.client.get('/')
        with self.client.session_transaction() as session:
            self.csrf = session['csrf']
        self.rows = [{'record_id':'GCA_021491695.1','run_id':'demo','score':1},
                     {'record_id':'GCA_030168515.1','run_id':'demo','score':2},
                     {'record_id':'GCA_021491695.1','run_id':'other','score':3},
                     {'record_id':'unmatched','run_id':'demo','score':4}]
        self.url = self.ingest(self.rows)
        self.entries = [{'record_id':self.rows[0]['record_id'],'run_id':'demo','ontology':o,'score':s}
                        for o,s in [('ENVO',None),('MONDO',1),('UBERON',0.3)]]
        self.entries += [{'record_id':'absent','run_id':'demo','ontology':'MONDO'}]

    def tearDown(self):
        self.temp.cleanup()

    def ingest(self, rows):
        response = self.client.post('/import', data={'csrf':self.csrf,'file':(io.BytesIO('\n'.join(json.dumps(x) for x in rows).encode()),'dataset.jsonl')})
        self.assertEqual(response.status_code,302)
        return response.location

    def attach(self, entries=None, raw=None, url=None, **extra):
        if raw is None:
            raw = '\n'.join(json.dumps(x) for x in (self.entries if entries is None else entries)).encode()
        data = {'csrf':self.csrf,'name':'Evaluation','record_field':'/record_id','run_field':'/run_id','file':(io.BytesIO(raw),'report.jsonl'),**extra}
        return self.client.post((url or self.url)+'/reports', data=data)

    def exported(self, **args):
        response = self.client.get(self.url+'/export?'+urlencode(args,doseq=True))
        self.assertEqual(response.status_code,200)
        return [json.loads(line) for line in response.data.splitlines()]

    def counts(self):
        conn=database.connect(self.path)
        try:return [conn.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('result_reports','report_entries','report_matches','records')]
        finally:conn.close()

    def test_exact_pair_matching_slices_and_details(self):
        response=self.attach();self.assertEqual(response.status_code,303)
        page=self.client.get(response.location)
        self.assertEqual(page.status_code,200)
        self.assertEqual(self.counts(),[1,4,3,4])
        self.assertEqual(self.exported(report_id=1,report_scope='matched'),self.rows[:1])
        self.assertEqual(self.exported(report_id=1,report_scope='unmatched'),self.rows[1:])
        self.assertEqual(self.exported(report_id=1,report_scope='all'),self.rows)
        detail=self.client.get(self.url+'/records/1').data
        self.assertIn(b'Result Report',detail)
        for ontology in [b'ENVO',b'MONDO',b'UBERON']:self.assertIn(ontology,detail)
        self.assertNotIn(b'id="result-report-tab"',self.client.get(self.url+'/records/3').data)
        unmatched=self.client.get(self.url+'/reports/1/download?unmatched=1')
        self.assertEqual([json.loads(line) for line in unmatched.data.splitlines()],[self.entries[-1]])
        self.assertEqual([json.loads(line) for line in self.client.get(self.url+'/reports/1/download').data.splitlines()],self.entries)

    def test_report_slice_combines_with_filters_graph_sort_and_paging(self):
        self.attach(self.entries+[{'record_id':self.rows[1]['record_id'],'run_id':'demo'}])
        self.assertEqual(self.exported(report_id=1,report_scope='matched',f='/score',op='gt',v='1'),self.rows[1:2])
        self.assertEqual(self.exported(report_id=1,sort='/score',direction='desc'),list(reversed(self.rows[:2])))
        graph=self.client.get(self.url+'/graph?report_id=1&report_scope=matched&format=json').json
        self.assertEqual(graph['scope']['matching_records'],2)
        self.assertEqual(self.exported(report_id=1,report_run='other'),[])
        for query in ['report_id=no','report_scope=invalid','report_id=999']:
            self.assertIn(self.client.get(self.url+'?'+query).status_code,[400,404])
            self.assertIn(self.client.get(self.url+'/export?'+query).status_code,[400,404])
            self.assertIn(self.client.get(self.url+'/graph?'+query).status_code,[400,404])

    def test_multiple_reports_runs_and_ambiguous_pairs(self):
        self.attach()
        self.attach([{'record_id':self.rows[0]['record_id'],'run_id':'other','ontology':'MONDO'}])
        self.assertEqual(self.exported(report_scope='matched'),[self.rows[0],self.rows[2]])
        self.assertEqual(self.exported(report_id=2),self.rows[2:3])
        self.assertEqual(self.exported(report_scope='matched',report_run='other'),self.rows[2:3])
        other=self.ingest([self.rows[0],self.rows[0]])
        self.attach(url=other)
        conn=database.connect(self.path)
        try:self.assertEqual(tuple(conn.execute('SELECT matched_records,matched_entries FROM result_reports WHERE id=3').fetchone()),(2,3))
        finally:conn.close()

    def test_invalid_report_rolls_back_and_requires_keys(self):
        invalid=[b'',b'[]',b'{"record_id":"a"}',b'{"run_id":"demo","record_id":1}',b'{"run_id":" ","record_id":"a"}',b'{"run_id":"demo","record_id":"a","x":NaN}',b'{"run_id":"demo","record_id":"a","x":1e400}',b'{"run_id":"demo","run_id":"demo","record_id":"a"}',b'\xff']
        for raw in invalid:
            response=self.attach(raw=json.dumps(self.entries[0]).encode()+b'\n'+raw if raw else raw)
            self.assertEqual(response.status_code,400,raw)
            self.assertEqual(self.counts(),[0,0,0,4])
        with patch('report_store.MAX_REPORT_LINE',10):self.assertEqual(self.attach().status_code,400)
        with patch('report_store.MAX_REPORT_ROWS',1):self.assertEqual(self.attach().status_code,400)
        self.assertEqual(self.counts(),[0,0,0,4])
        self.assertEqual(self.attach(run_field='/absent').status_code,400)
        self.assertEqual(self.attach(record_field='/run_id').status_code,400)
        self.assertEqual(self.attach(record_field='/score').status_code,400)

    def test_bom_blank_lines_case_version_and_no_run_guessing(self):
        raw=b'\xef\xbb\xbf\n'+json.dumps(self.entries[0]).encode()+b'\n\n'
        self.assertEqual(self.attach(raw=raw).status_code,303)
        self.attach([{'record_id':'gca_021491695.1','run_id':'demo'}, {'record_id':'GCA_021491695','run_id':'demo'}, {'record_id':'GCA_021491695.1','run_id':'Demo'}])
        self.assertEqual(self.exported(report_id=2),[])
        no_run=self.ingest([{'record_id':'GCA_021491695.1','other':'demo'}])
        self.assertEqual(self.attach(url=no_run).status_code,400)

    def test_nested_mapping_and_dataset_isolation(self):
        url=self.ingest([{'genome':{'accession':'GCA_021491695.1'},'execution':{'run':'demo'}}])
        response=self.attach(url=url,record_field='/genome/accession',run_field='/execution/run')
        self.assertEqual(response.status_code,303)
        self.assertEqual(self.client.get(self.url+'?report_id=1').status_code,404)
        self.assertEqual(self.client.get(self.url+'/reports/1/download').status_code,404)
        self.assertEqual(self.client.post(self.url+'/reports/1/delete',data={'csrf':self.csrf,'confirm':'delete'}).status_code,404)
        self.assertNotIn(b'id="result-report-tab"',self.client.get(self.url+'/records/1').data)
        self.assertEqual(self.client.get(url+'/records/1').status_code,404)

    def test_delete_attachment_confirmation_preserves_rows_comments_other_reports(self):
        self.attach();self.attach()
        self.client.post(self.url+'/records/1/comment',data={'csrf':self.csrf,'comment':'keep'})
        endpoint=self.url+'/reports/1/delete'
        self.assertEqual(self.client.post(endpoint,data={'confirm':'delete'}).status_code,400)
        self.assertEqual(self.client.post(endpoint,data={'csrf':self.csrf}).status_code,400)
        self.assertEqual(self.client.get(endpoint).status_code,405)
        self.assertEqual(self.client.post(endpoint,data={'csrf':self.csrf,'confirm':'delete'}).status_code,303)
        self.assertEqual(self.counts(),[1,4,3,4])
        self.assertEqual(self.exported()[0]['comments'],'keep')
        self.assertEqual(self.client.get(self.url+'?report_id=1').status_code,404)
        database.initialize(self.path)
        self.assertEqual(self.counts(),[1,4,3,4])
        self.client.post(self.url+'/reports/2/delete',data={'csrf':self.csrf,'confirm':'delete'})
        self.assertNotIn(b'id="result-report-tab"',self.client.get(self.url+'/records/1').data)

    def test_dataset_delete_cascades_and_upload_csrf(self):
        self.attach()
        self.assertEqual(self.client.post(self.url+'/reports',data={}).status_code,400)
        self.client.post(self.url+'/delete',data={'csrf':self.csrf,'confirm':'delete'})
        self.assertEqual(self.counts(),[0,0,0,0])
        conn=database.connect(self.path)
        try:self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])
        finally:conn.close()

    def test_details_escape_html_paginate_and_limit_selected_report(self):
        entry={**self.entries[0],'ontology':'<script>alert(1)</script>'}
        self.attach([entry]*26)
        page=self.client.get(self.url+'/records/1').data
        self.assertNotIn(b'<script>alert(1)</script>',page)
        self.assertIn(b'&lt;script&gt;',page)
        self.assertIn(b'Next results',page)
        page=self.client.get(self.url+'/records/1?result_page=2').data
        self.assertIn(b'source line 26',page)
        self.assertNotIn(b'source line 25',page)
        self.assertEqual(self.client.get(self.url+'/records/1?result_page=bad').status_code,400)
        self.attach([{'record_id':self.rows[1]['record_id'],'run_id':'demo'}])
        self.assertIn(b'No results for the selected report/run',self.client.get(self.url+'/records/1?report_id=2').data)

    def test_demo_creates_separate_dataset_two_matched_rows_six_entries(self):
        response=self.client.post('/demo/reports',data={'csrf':self.csrf})
        self.assertEqual(response.status_code,303)
        self.assertEqual(self.client.get(response.location).status_code,200)
        self.assertEqual(self.counts(),[1,6,6,8])
        self.assertEqual(self.exported(),self.rows)


if __name__=='__main__':unittest.main()
