import json
from pathlib import Path
import tempfile
import unittest
import fitz

from pdf_import.importer import parse_pdf,parse_text,detect_status,resolve_program

class TestImporter(unittest.TestCase):
    def write_pdf(self, txt):
        doc=fitz.open();page=doc.new_page()
        # 使用PyMuPDF內建繁體中文字型，測試不依賴特定Linux字型路徑。
        y=65
        for line in txt.splitlines():
            page.insert_text((48,y),line,fontname='china-t',fontsize=10);y+=18
        tmp=tempfile.NamedTemporaryFile(suffix='.pdf',delete=False)
        tmp.close();doc.save(tmp.name);doc.close();self.addCleanup(lambda:Path(tmp.name).unlink(missing_ok=True))
        return tmp.name
    def test_status_order(self):
        self.assertEqual(detect_status('未通過 Failed'),'failed')
        self.assertEqual(detect_status('通過 Passed'),'passed')
        self.assertEqual(detect_status('未到(Not Entered)'),'in_progress')
        self.assertEqual(detect_status('停修'),'withdrawn')
        self.assertEqual(detect_status('未公布'),'needs_review')
    def test_department_detected_and_double_major_not_leaked(self):
        pdf=self.write_pdf('113-1-2-3-經濟學系\n10000 - 主系\n完成 12101 - 系訂必修\n1131 EC2003 個體經濟學 3 通過\n1132 EC2004 個體經濟學 3 未通過\n30000 - 雙主修\n1132 MA1001 微積分 4 通過')
        out=parse_pdf(pdf)
        self.assertEqual(out['program_id'],'economics_113')
        self.assertEqual(len(out['courses']),2)
        self.assertEqual(out['courses'][1]['status'],'failed')
        self.assertNotIn('MA1001',str(out))
    def test_ambiguous_track_rejected(self):
        with self.assertRaisesRegex(ValueError,'多個組別'):
            resolve_program('數學系',113)
    def test_wrong_program_id_rejected(self):
        with self.assertRaises(ValueError):
            resolve_program('經濟學系',113,'physics_113')
    def test_unknown_status_rejected(self):
        pdf=self.write_pdf('113-1-2-3-經濟學系\n10000 - 主系\n1131 EC2003 個體經濟學 3 未公布')
        with self.assertRaisesRegex(ValueError,'狀態不明'):
            parse_pdf(pdf)
    def test_duplicate_same_term_deduplicated(self):
        pdf=self.write_pdf('113-1-2-3-經濟學系\n10000 - 主系\n完成 12101 - 類別甲\n1131 EC2003 個體經濟學 3 通過\n完成 12102 - 類別乙\n1131 EC2003 個體經濟學 3 通過')
        out=parse_pdf(pdf)
        self.assertEqual(len(out['courses']),1)
        self.assertEqual(set(out['courses'][0]['listed_under_rule_codes']),{'12101','12102'})
    def test_not_an_audit_pdf_rejected(self):
        pdf=self.write_pdf('113-1-2-3-經濟學系\n1131 EC2003 個體經濟學 3 通過')
        with self.assertRaises(ValueError):parse_pdf(pdf)

    def test_teacher_program_is_excluded_and_private_header_not_exported(self):
        text = '學號：999999999 姓名：測試同學\n113-1-2-3-經濟學系\n10000 - 主系\n完成 12101 - 必修\n1131 EC2003 個體經濟學 3 81.25 通過\n50000 - 教育學程\n1132 EP1002 教育哲學 2 90.00 通過'
        out=parse_text(text)
        self.assertEqual(len(out['courses']),1)
        for private in ['999999999','測試同學','81.25','EP1002']:
            self.assertNotIn(private,str(out))
    def test_math_program_alias_and_explicit_track(self):
        selected=resolve_program('數學系(計算與資料科學組)',113,'math_datascience_113')
        self.assertEqual(selected['program_id'],'math_datascience_113_official')
        with self.assertRaises(ValueError):
            resolve_program('數學系(數學科學組)',113,'math_datascience_113')
    def test_conflicting_same_term_duplicate_rejected(self):
        text='113-1-2-3-經濟學系\n10000 - 主系\n完成 12101 - 必修\n1131 EC2003 個體經濟學 3 通過\n1131 EC2003 個體經濟學 3 未通過'
        with self.assertRaisesRegex(ValueError,'衝突'):
            parse_text(text)

if __name__=='__main__': unittest.main()
