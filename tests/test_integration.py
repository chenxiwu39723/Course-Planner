import os
import unittest
from import_graduation_pdf import parse_text,parse_pdf
from analyze_graduation import analyze_pdf
class TestImport(unittest.TestCase):
    def test_failed_before_passed_and_exclude_same_page_program(self):
        t='113-1-2001-2-數學系(計算與資料科學組)\n10000 - 主系\n0學分，0門課，應修128學分\n未完成 12101 - 必修\n1131 MA2011 統計學 3 30.00 未通過\n114-5-0001-0-中等教師\n50000 - 教育學程\n1141 EP1002 教育哲學 2 87.00 通過'
        s=parse_text(t)
        self.assertEqual(len(s['courses']),1)
        self.assertEqual(s['courses'][0]['status'],'failed')
        self.assertNotIn('30.00',str(s))
    @unittest.skipUnless(os.environ.get('AUDIT_PDF'),'設定AUDIT_PDF可測試實際PDF')
    def test_actual_uploaded_pdf(self):
        path=os.environ['AUDIT_PDF']
        s=parse_pdf(path)
        self.assertEqual(len(s['courses']),49)
        self.assertFalse(any(c['course_id'].startswith('EP') for c in s['courses']))
        self.assertEqual(s['main_program_total']['completed_credits_as_reported'],74)
        self.assertEqual(analyze_pdf(path,True)['total_credits']['completed'],77)
        self.assertEqual(sum(c['credits'] for c in s['courses'] if c['status']=='in_progress'),24)
        self.assertEqual(s['learning_passport_hours'],85)
if __name__=='__main__': unittest.main()
