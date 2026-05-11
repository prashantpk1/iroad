import re
import pathlib
root = pathlib.Path('.')
for path in sorted(root.rglob('*.html')):
    if '.history' in path.parts:
        continue
    text = path.read_text(encoding='utf-8')
    if re.search(r'class=\"(?:text-anime-style-[123]|text-effect|wow fadeInUp)\"|data-cursor=\"-opaque\"|data-wow-delay=\"[^\"]*\"', text):
        print(path)
        for i, line in enumerate(text.splitlines(), 1):
            if re.search(r'class=\"(?:text-anime-style-[123]|text-effect|wow fadeInUp)\"|data-cursor=\"-opaque\"|data-wow-delay=\"[^\"]*\"', line):
                print(f'  {i}: {line.strip()}')
