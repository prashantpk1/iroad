import re
from pathlib import Path

path = Path(__file__).resolve().parent / 'iroad_frontend' / 'templates' / 'iroad_frontend' / 'home' / 'index.html'
text = path.read_text(encoding='utf-8')
skip_bases = {'features_rating_value', 'about_experience_number'}


def repl(m):
    base = m.group(1)
    if base in skip_bases:
        return m.group(0)
    return "{{%% cms_txt home '%s' %%}}" % base


new_text, n = re.subn(r'\{\{\s*home\.([a-z0-9_]+)_en\s*\}\}', repl, text)
path.write_text(new_text, encoding='utf-8')
print('replacements', n)
