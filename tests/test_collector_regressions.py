"""법제처 2026-11-10 수집 실패의 최소 발췌. 네트워크/코퍼스 불필요."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from collect_regulations import html_to_text
from collect_rules import split_articles
from scripts import apply_structure_titles as adapter


class HtmlMarksTest(unittest.TestCase):
    def test_korean_marks_survive_real_and_escaped_tags(self):
        for mark in ('개정 2025.02.18.', '삭제 2021. 1. 1.', '신설 2022.06.15',
                     '제목개정 2018. 8. 1.', '전문개정 2020. 1. 1.'):
            with self.subTest(mark=mark):
                self.assertEqual(html_to_text(
                    f'<p><span>&lt;{mark}&gt;</span></p>'), f'<{mark}>')
                self.assertEqual(html_to_text(f'<p><{mark}></p>'), f'<{mark}>')
        self.assertEqual(html_to_text(
            '<script>noise</script><style>noise</style><!-- noise -->'
            '<p>&lt;TD class="x"&gt;내용&lt;/TD&gt;&lt;br/&gt;</p>'), '내용')

    def test_deleted_articles_have_nonempty_bodies(self):
        # 교육대학원 제8조 / 교원 인사 제29조 원문 표기.
        text = html_to_text(
            '<p><label>제8조</label><span>&lt;삭제, 2020. 6. 3.&gt;</span></p>'
            '<p><label>제29조</label><span>&lt;삭제 2021. 1. 1.&gt;</span></p>')
        self.assertEqual([a['본문'] for a in split_articles(text)],
                         ['<삭제, 2020. 6. 3.>', '<삭제 2021. 1. 1.>'])


class NestedTitlesTest(unittest.TestCase):
    def test_real_titles(self):
        for number, title in (
            ('제6조', '학과(부)의 장 등'),
            ('제50조의2', '융합학과(부) 및 융합전공'),
            ('제18조의2', '학과(부) 심사위원회'),
            ('제12조', '대학(원) 등 공채관리위원회'),
            ('제3조의19', 'G-램프(LAMP)사업단'),
            ('제3조의20', '지역성장 인재양성체계(ANCHOR)사업단'),
            # 기존 지침 key_2637 / key_4488도 같은 정규식을 쓴다.
            ('제7조', '편입학(전과)생 수용'),
            ('제9조', 'ARS(자동응답시스템), 보이스봇'),
        ):
            with self.subTest(title=title):
                self.assertEqual(split_articles(f'{number}({title}) ① 본문\n② 후속'),
                                 [{'조문번호': number, '조문제목': title, '본문': '① 본문\n② 후속'}])

    def test_existing_formats_and_markdown_prefixes(self):
        for prefix, suffix in [('', ''), ('### ', ''), ('- ', ''), ('* ', ''),
                               ('+ ', ''), ('> ', ''), ('**', '**'), ('__', '__'),
                               ('## - **', '**')]:
            for title in ('(목적)', '（목적）', '【목적】', '（학과（부）의 장 등）',
                          '（학과(부)의 장 등）', '【학과(부)의 장 등】'):
                with self.subTest(prefix=prefix, title=title):
                    self.assertEqual(split_articles(f'{prefix}제 6 조 의 2{title}{suffix} 본문'),
                                     [{'조문번호': '제6조의2', '조문제목': title[1:-1], '본문': '본문'}])
        self.assertEqual(split_articles('제1조 본문\n제2조(목적) 다음'), [
            {'조문번호': '제1조', '조문제목': '', '본문': '본문'},
            {'조문번호': '제2조', '조문제목': '목적', '본문': '다음'}])


class StructureMarksTest(unittest.TestCase):
    def apply(self, body):
        rows = [{'규정명': '테스트', '조문번호': '제1조', '본문': body, 'record_id': 'stable'},
                {'규정명': '테스트', '조문번호': '제2조', '본문': '다음 조문', 'record_id': 'next'}]
        result, _ = adapter.apply_to_corpus(rows)
        result[0]['본문'] = adapter.restore_article_marks(body, result[0]['본문'])
        self.assertEqual([r['record_id'] for r in result], ['stable', 'next'])
        return result

    def test_five_observed_tails(self):
        # 본문 끝 표시와 다음 편제 제목만 발췌.
        for name, prefix, mark, heading in (
            ('학칙 제43조', '따로 정할 수 있다.', '<신설 2022.06.15>', '제2절 대학 학사운영'),
            ('문화전문대학원 제26조의3', '제출하게 할 수 있다.', '<개정 2026. 7. 30.>', '제6장 지도교수'),
            ('교육대학원 제11조', '', '<삭제, 2020. 6. 3.>', '제 3 장 교육과정, 개강과목, 수강신청, 성적, 수료'),
            ('교원 인사 제25조', '임용을 유예할 수 있다.', '<개정 2024. 2. 1.>', '제4장 재계약'),
            ('교원 인사 제9조', '위원장이 지명한다.', '<제목개정 2018. 8. 1.>', '제3장 신규채용'),
        ):
            with self.subTest(name=name):
                expected = '\n'.join(filter(None, (prefix, mark)))
                rows = self.apply(expected + '\n' + heading)
                self.assertEqual(rows[0]['본문'], expected)
                self.assertIn(heading, (rows[1]['장'], rows[1]['절']))
                again, _ = adapter.apply_to_corpus(rows)
                self.assertEqual(adapter.restore_article_marks(expected, again[0]['본문']), expected)

    def test_heading_marks_stay_removed(self):
        for tail in ('제13장 특임교수<신설 2026.3.23.>',
                     '제 4 장 재계약\n<개정 2023. 2. 10.>',
                     '제4장 재계약\n<개정 2023. 2. 10.>\n제1절 절 제목'):
            with self.subTest(tail=tail):
                self.assertEqual(self.apply('본문\n' + tail)[0]['본문'], '본문')
                self.assertEqual(self.apply('본문\n<전문개정 2020. 1. 1.>\n' + tail)[0]['본문'],
                                 '본문\n<전문개정 2020. 1. 1.>')

    def test_repeated_marks_and_unchanged_output(self):
        mark = '<개정 2024. 2. 1.>'
        body = f'{mark}\n본문\n{mark}\n\n<신설 2022.06.15>\n제2절 대학 학사운영'
        self.assertEqual(self.apply(body)[0]['본문'], f'{mark}\n본문\n{mark}\n<신설 2022.06.15>')
        self.assertEqual(adapter.restore_article_marks(body, body), body)
        self.assertEqual(adapter.restore_article_marks(body, '다른 본문'), '다른 본문')
        self.assertEqual(self.apply('본문\n' + mark)[0]['본문'], '본문\n' + mark)

    def test_main_restores_marks_and_rejects_id_changes_before_write(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            corpus = Path(directory) / 'fixture.json'
            row = {'규정명': '테스트', '본문': '<삭제, 2020. 6. 3.>\n제3장 교육과정',
                   'record_id': 'stable', 'revision': '1', 'record_type': 'article'}
            corpus.write_text(json.dumps([row]), encoding='utf-8')
            with patch.object(adapter, 'CORPUS', corpus), patch('builtins.print'):
                self.assertEqual(adapter.main(), 0)
                saved = json.loads(corpus.read_text())
                self.assertEqual(saved[0]['본문'], '<삭제, 2020. 6. 3.>')
                self.assertEqual(saved[0]['record_id'], 'stable')
                previous = corpus.read_bytes()
                with patch.object(adapter, 'apply_to_corpus', return_value=([{**row, 'record_id': 'changed'}], {})):
                    self.assertEqual(adapter.main(), 1)
                self.assertEqual(corpus.read_bytes(), previous)


if __name__ == '__main__':
    unittest.main()
