import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from app import create_app
from graph_views import repository_rows, REPO_INPUTS
from relationships import Graph, tokens
import database


def project(graph, obj, rid=1):
    graph.add_record({'id': rid, 'line_number': rid}, obj)
    return graph.result()


class RelationshipTest(unittest.TestCase):
    def test_explicit_identifiers_and_provenance(self):
        graph = Graph()
        result = project(graph, {
            'genome': {'accession': 'GCA_000590395.1', 'organism': {'taxId': 1280, 'organismName': 'S. aureus'},
                       'assemblyInfo': {'bioprojectAccession': 'PRJNA239406', 'biosample': {'accession': 'SAMN02665331', 'hostDisease': 'bacteremia'}}},
            'bioprojects': [{'accession': 'PRJNA239406', 'publications': [{'db': 'Pubmed', 'id': '24744328', 'pubdate': '2014'}]}],
            'sra_runs': [{'run_accession': 'SRR123456', 'experiment_accession': 'SRX123456', 'sample_accession': 'SRS123456', 'study_accession': 'SRP123456'}],
        })
        self.assertEqual({n['type'] for n in result['nodes']}, {'study','sample','sequence','organism','disease','publication','repository'})
        self.assertIn('publication:PMID:24744328', graph.nodes)
        self.assertNotIn('publication:PMID:2014', graph.nodes)
        links = {(e['source'], e['target'], e['relation']): e for e in result['edges']}
        edge = links[('sequence:GCA_000590395.1','sample:SAMN02665331','sequenced from')]
        self.assertEqual(edge['evidence'][0]['path'], '/genome/assemblyInfo/biosample/accession')
        self.assertEqual(edge['evidence'][0]['record_id'], 1)
        self.assertIn(('sequence:SRR123456','sequence:SRX123456','run of experiment'), links)
        self.assertFalse(any(e['source']=='sequence:GCA_000590395.1' and e['target']=='sequence:SRR123456' for e in result['edges']))
        self.assertTrue(all(e['status']=='derived' for e in result['edges'] if e['relation']=='registered in'))

    def test_flat_keys_and_duplicate_records(self):
        graph = Graph()
        obj = {'genome.assembly_accession': 'GCF_000590395.1', 'genome.biosample_accession': 'SAMN02665331', 'genome.publication':'24744328; 24744329'}
        project(graph, obj)
        project(graph, obj, 2)
        self.assertEqual(graph.unsupported, 0)
        edge = next(e for e in graph.edges.values() if e['relation']=='sequenced from')
        self.assertEqual([e['record_id'] for e in edge['evidence']], [1,2])
        self.assertEqual(edge['evidence'][0]['path'], '/genome.biosample_accession')
        self.assertEqual(len([n for n in graph.nodes.values() if n['type']=='publication']), 2)

    def test_no_inference_from_prose_host_or_affiliation(self):
        graph = Graph()
        result = project(graph, {'record_id':'SAMN123456', 'host':'Homo sapiens', 'comments':['host_disease: infection'], 'description':'Supported by NIAID. HIV study PRJNA123456.', 'disease':'not provided', 'candidate_curies':['MONDO:0000001']})
        self.assertEqual({n['type'] for n in result['nodes']}, {'sample','repository'})
        self.assertEqual(tokens('SAMN123456 PRJNA123456 SRP123456', 'sequence'), [])
        self.assertEqual(tokens('GCA_000590395.1 SRR123456 NC_012345.1', 'sequence'), ['GCA_000590395.1','SRR123456','NC_012345.1'])

    def test_proposals_join_only_exact_id_and_retrieved_terms(self):
        source = {'record_id':'SAMN123456','extras':{'taxonomy_id':1280,'organism':'S. aureus','attributes':{'host_disease':'MRSA infection'}}}
        proposal = {'record_id':'SAMN123456','candidate_curies':['MONDO:0100073'], 'terms':[{'ontology':'MONDO','term_id':'MONDO:0100073','label':'MRSA infection'}, {'ontology':'MONDO','term_id':'MONDO:9999999','label':'unretrieved'}, {'ontology':'UBERON','term_id':'UBERON:0000001','label':'tissue'}]}
        graph=Graph(); result=project(graph, {'source_record':source,'proposal':proposal})
        proposed=[e for e in result['edges'] if e['status']=='proposed']
        self.assertEqual(len(proposed),1)
        self.assertEqual(proposed[0]['evidence'][0]['path'],'/proposal/terms/0')
        self.assertIn('disease:reported:mrsa infection',graph.nodes)
        self.assertIn('disease:MONDO:0100073',graph.nodes)
        proposal['record_id']='SAMN999999'; graph=Graph()
        self.assertFalse(any(e['status']=='proposed' for e in project(graph, {'source_record':source,'proposal':proposal})['edges']))

    def test_root_metadata_does_not_attach_to_arbitrary_nested_entities(self):
        graph=Graph()
        result=project(graph, {"biosamples":[{"accession":"SAMN123456"},{"accession":"SAMN234567"}],"disease":"unassigned","niaid_program":"unassigned","extras":{"taxonomy_id":9606}})
        self.assertEqual({n["type"] for n in result["nodes"]}, {"sample","repository"})

    def test_null_and_malformed_optional_metadata(self):
        graph=Graph()
        result=project(graph, {'record_id':'SAMN123456','terms':None,'candidate_curies':None,'genome':[None,5],'biosamples':None,'original_metadata':[], 'extras':{'organism':[], 'taxonomy_id':{}, 'attributes':None}})
        self.assertNotIn('disease', {n['type'] for n in result['nodes']})
        self.assertEqual(project(Graph(), {'metric':0.7})['unsupported_records'],1)

    def test_explicit_program_and_bounds(self):
        graph=Graph()
        result=project(graph, {'record_id':'SAMN123456','niaid_programs':['Example explicit program']})
        edge=next(e for e in result['edges'] if e['relation']=='reported NIAID program')
        self.assertEqual(edge['evidence'][0]['path'],'/niaid_programs')
        graph=Graph(max_nodes=8,max_edges=6)
        result=project(graph, {'biosamples':[{'accession':f'SAMN{100000+i}','disease':'disease '+str(i)} for i in range(150)]})
        self.assertLessEqual(len(result['nodes']),8);self.assertLessEqual(len(result['edges']),6)
        self.assertTrue(result['truncated'])
        self.assertTrue(all(e['source'] in graph.nodes and e['target'] in graph.nodes for e in result['edges']))


class GraphRouteTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.app=create_app({'TESTING':True,'DATABASE':str(self.root/'db.sqlite3'),'SECRET_KEY':'test','REPOSITORY_ROOT':str(self.root)})
        self.client=self.app.test_client();self.client.get('/')
        with self.client.session_transaction() as session:self.csrf=session['csrf']

    def tearDown(self):self.temp.cleanup()

    def ingest(self, rows):
        response=self.client.post('/import',data={'csrf':self.csrf,'name':'Graph fixture','file':(io.BytesIO(('\n'.join(json.dumps(r) for r in rows)).encode()),'graph.jsonl')})
        self.assertEqual(response.status_code,302)
        return response.location

    def test_filters_focus_navigation_and_export(self):
        url=self.ingest([{'record_id':'SAMN123456','disease':'alpha'},{'record_id':'SAMN234567','disease':'beta'}])
        page=self.client.get(url+'/graph');self.assertEqual(page.status_code,200)
        self.assertIn(b'graph-data',page.data);self.assertIn(b'Relationship graph',self.client.get(url).data)
        self.assertIn(b'View connections',self.client.get(url+'/records/1').data)
        query=urlencode({'f':'/disease','op':'eq','v':'beta','format':'json'})
        response=self.client.get(url+'/graph?'+query)
        self.assertEqual(response.json['scope']['records_examined'],1)
        self.assertIn('sample:SAMN234567',{n['id'] for n in response.json['nodes']})
        self.assertNotIn('sample:SAMN123456',{n['id'] for n in response.json['nodes']})
        self.assertIn('attachment',response.headers['Content-Disposition'])
        focused=self.client.get(url+'/graph?record_id=1&format=json').json
        self.assertEqual(focused['scope']['matching_records'],1)
        other=self.ingest([{'record_id':'SAMN345678'}])
        self.assertEqual(self.client.get(other+'/graph?record_id=1').status_code,404)
        for query in ['graph_size=100000','graph_page=no','f=/no&op=eq&v=x','sort=no','record_id=no']:
            self.assertEqual(self.client.get(url+'/graph?'+query).status_code,400)

    def test_pagination_byte_budget_and_safe_html(self):
        url=self.ingest([{'record_id':f'SAMN{100000+i}','disease':'</script><script>alert(1)</script>'} for i in range(26)])
        response=self.client.get(url+'/graph?graph_page=2&format=json').json
        self.assertEqual(response['scope']['records_examined'],1)
        self.assertIn('sample:SAMN100025',{n['id'] for n in response['nodes']})
        self.assertNotIn(b'</script><script>alert(1)',self.client.get(url+'/graph').data)
        with patch('graph_views.MAX_GRAPH_BYTES',1):
            result=self.client.get(url+'/graph?format=json').json
        self.assertEqual(result['scope']['records_skipped_for_size'],25)
        self.assertEqual(result['nodes'],[])

    def write_repo(self, raw, proposals):
        for path,rows in zip(REPO_INPUTS,(raw,proposals)):
            target=self.root/path;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text('\n'.join(json.dumps(row) for row in rows))

    def test_repository_import_exact_join_csrf_and_source_preservation(self):
        raw=[{'record_id':'SAMN123456','extras':{'organism':'S. aureus','taxonomy_id':1280}}]
        proposals=[{'record_id':'SAMN123456','candidate_curies':['MONDO:0100073'],'terms':[{'ontology':'MONDO','term_id':'MONDO:0100073','label':'MRSA'}]}, {'record_id':'SAMN999999'}]
        self.write_repo(raw,proposals)
        self.assertEqual(self.client.post('/import/repository-graph').status_code,400)
        result=self.client.post('/import/repository-graph',data={'csrf':self.csrf})
        self.assertEqual(result.status_code,303)
        graph=self.client.get(result.location+'?format=json').json
        self.assertEqual(graph['scope']['records_examined'],1)
        self.assertTrue(any(e['status']=='proposed' for e in graph['edges']))
        dataset_url=result.location.removesuffix('/graph')
        source=json.loads(self.client.get(dataset_url+'/export').data)
        self.assertEqual(source['source_record'],raw[0]);self.assertEqual(source['proposal'],proposals[0])
        self.assertEqual(source['source_files'],list(REPO_INPUTS))
        self.assertEqual(len(repository_rows(self.root)[0]),1)
        self.assertEqual(repository_rows(self.root)[2],1)

    def test_repository_missing_or_ambiguous_fails_without_dataset(self):
        self.assertEqual(self.client.post('/import/repository-graph',data={'csrf':self.csrf}).status_code,400)
        self.write_repo([{'record_id':'SAMN123456'},{'record_id':'SAMN123456'}],[])
        self.assertEqual(self.client.post('/import/repository-graph',data={'csrf':self.csrf}).status_code,400)
        conn=database.connect(self.root/'db.sqlite3')
        try:self.assertEqual(conn.execute('SELECT count(*) FROM datasets').fetchone()[0],0)
        finally:conn.close()
