import os
import re

def rewrite_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # match "import aria_code.<mod>" -> "from aria_code import <mod>"
    # but handle cases with multiple imports carefully
    content = re.sub(r'^import aria_code\.([a-zA-Z0-9_]+)(\s|\n)', r'from aria_code import \1\2', content, flags=re.MULTILINE)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

for root, _, files in os.walk("src/aria_code"):
    for file in files:
        if file.endswith(".py"):
            rewrite_file(os.path.join(root, file))

for root, _, files in os.walk("tests"):
    for file in files:
        if file.endswith(".py"):
            rewrite_file(os.path.join(root, file))
