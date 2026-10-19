import ast
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pandas as pd
import sys
ROOT = Path(__file__).parents[1]
sys.path.insert(0,str(ROOT))
from tools.scheduled_monthly.southern_exports import export_south_sources


def load_functions(filename, names):
    source=ROOT/'tools/scheduled_monthly'/filename
    nodes=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]
    nodes += [n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name in names]
    ns=dict(FUNCTION_NAME="預收",os=os,tempfile=tempfile,pd=pd,export_south_sources=export_south_sources,log=lambda *a:None)
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),str(source),'exec'),ns)
    return ns


class SouthernTests(unittest.TestCase):
    def test_order_source_names_sequence_append_order_and_dedup(self):
        for label in ('訂單','預收','已退款全部加收','已退款全部退款'):
            calls=[]
            def download(region):
                calls.append(region)
                if region=='高雄':
                    self.assertTrue((Path(temp_dir)/f'202609-2-{label}-原台南.xlsx').exists())
                return pd.DataFrame({'訂單編號':['LC1','LC2'] if region=='高雄' else ['LC2','LC3'],'金額':[100,200] if region=='高雄' else [200,300]})
            with tempfile.TemporaryDirectory() as temp_dir:
                result,paths,final=export_south_sources(download,temp_dir,'202609-2',label)
                self.assertEqual(calls,['台南','高雄'])
                self.assertEqual([Path(p).name for p in paths],[f'202609-2-{label}-原台南.xlsx',f'202609-2-{label}-原高雄.xlsx'])
                self.assertEqual(Path(final).name,f'202609-2-{label}-高雄.xlsx')
                self.assertEqual(result['訂單編號'].tolist(),['LC1','LC2','LC3'])
                self.assertEqual(pd.read_excel(paths[0])['訂單編號'].tolist(),['LC2','LC3'])

    def test_empty_sources_keep_headers_and_failed_source_stops(self):
        with tempfile.TemporaryDirectory() as temp:
            result,_,_=export_south_sources(lambda region:pd.DataFrame(columns=['訂單編號']),temp,'202609-1','訂單')
            self.assertEqual(result.columns.tolist(),['訂單編號'])
            with self.assertRaises(RuntimeError): export_south_sources(lambda region:None,temp,'202609-1','訂單')
            with self.assertRaises(ValueError): export_south_sources(Mock(side_effect=ValueError('download failed')),temp,'202609-1','訂單')

    def test_refund_outputs_separate_charge_and_refund_with_six_files(self):
        ns=load_functions('refund_report.py',{'export_kaohsiung'})
        searches=[]
        ns['build_export_url']=lambda mode,start,end,region:(f'{mode}/{region}',mode)
        ns['download']=lambda session,url: searches.append(url) or url
        ns['read_excel_from_bytes']=lambda content:pd.DataFrame({'訂單編號':[content]})
        with tempfile.TemporaryDirectory() as temp:
            files=ns['export_kaohsiung'](object(),temp,'202609-2','start','end')
            self.assertEqual(searches,['charge/台南','charge/高雄','refund/台南','refund/高雄'])
            self.assertEqual(len(files),6)
            self.assertEqual(pd.read_excel(files[2])['類型'].tolist(),['加收','加收'])
            self.assertEqual(pd.read_excel(files[5])['類型'].tolist(),['退款','退款'])

    def test_prepaid_three_files_and_failed_download_not_uploaded(self):
        ns=load_functions('prepaid_report.py',{'process_city'})
        ns['requests']=SimpleNamespace(Session=lambda:object())
        for key in ('login','resolve_area_folder','get_or_create_single_child_folder','write_monthly_log'):
            ns[key]=Mock()
        calls=[]
        ns['download_single_export']=lambda session,region,rng:calls.append(region) or region
        ns['read_excel_from_bytes']=lambda region:pd.DataFrame({'訂單編號':[region]})
        uploaded=[]
        ns['upload_to_gdrive']=lambda service,path,folder:uploaded.append(Path(path).name)
        args=SimpleNamespace(folder_id='root')
        rng={'folder_tag':'202609-2','date_text':'test'}
        ns['process_city']('高雄',args,{'高雄':{'email':'test','password':'test'}},object(),rng)
        self.assertEqual(calls,['台南','高雄'])
        self.assertEqual(uploaded,['202609-2-預收-原台南.xlsx','202609-2-預收-原高雄.xlsx','202609-2-預收-高雄.xlsx'])
        uploaded.clear()
        ns['download_single_export']=Mock(side_effect=ValueError('failed'))
        with self.assertRaises(ValueError):ns['process_city']('高雄',args,{'高雄':{'email':'test','password':'test'}},object(),rng)
        self.assertEqual(uploaded,[])

if __name__=='__main__':unittest.main()
