"""Print the GitHub release notes of a version: how to install it, then its
CHANGELOG.md section, with the repository links pointing to the tag.

Usage: python3 .github/scripts/release_notes.py <version> <owner/repo>
"""
import re
import sys


def unwrap(markdown: str) -> str:
    """Join the lines of each paragraph and list item: GitHub release notes
    can render a single newline as a line break."""
    lines: list[str] = []
    fence = False
    for line in markdown.split('\n'):
        text = line.strip()
        if text.startswith('```'):
            fence = not fence
        starts_block = (fence or text.startswith('```') or not text
                        or text.startswith(('- ', '|', '#')))
        previous = lines[-1].strip() if lines else ''
        if (not starts_block and previous and not previous.startswith(('```', '|', '#'))):
            lines[-1] += ' ' + text
        else:
            lines.append(line)
    return '\n'.join(lines)


def main(version: str, repo: str) -> str:
    with open('CHANGELOG.md', encoding='utf-8') as f:
        changelog = f.read()
    section = re.search(rf'^## {re.escape(version)} \((?P<date>[^)]*)\)\n(?P<body>.*?)(?=^## |\Z)',
                        changelog, re.M | re.S)
    if not section:
        sys.exit(f'CHANGELOG.md has no section for {version}')
    if section['date'] == 'unreleased':
        sys.exit(f'The CHANGELOG.md section of {version} is still unreleased: date it first')
    # Relative links (README.md#gpu) only resolve inside the repository
    body = re.sub(r'\]\((?!https?:|#)([^)]+)\)',
                  lambda link: f'](https://github.com/{repo}/blob/v{version}/{link[1]})',
                  unwrap(section['body'].strip()))
    image = f'ghcr.io/{repo.split("/")[0].lower()}/easyvmaf'
    return (f'```bash\n'
            f'pipx install easyvmaf=={version}\n'
            f'docker pull {image}:{version}       # CPU\n'
            f'docker pull {image}:{version}-cuda  # CUDA\n'
            f'```\n\n'
            f'{body}\n')


if __name__ == '__main__':
    print(main(*sys.argv[1:]), end='')
