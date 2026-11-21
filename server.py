from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from datetime import datetime, timezone
import json
import re
import sqlite3

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / '04_爬蟲資料' / 'cgu_data' / 'cgu.db'
CONFIG_PATH = ROOT / 'search_config.json'
PORT = 8765


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {'suggestions': [], 'synonyms': {}, 'typos': {}, 'stopwords': [], 'boosts': []}


CONFIG = load_config()


def clean(value):
    return re.sub(r'[\s，、,.。／/：:；;！？!?（）()「」『』\[\]{}]+', '', (value or '').lower())


def validity(value):
    return 'yes' if value == 1 else 'no' if value == 0 else 'unknown'


def normalize_query(query):
    value = clean(query)
    for stopword in CONFIG.get('stopwords', []):
        value = value.replace(clean(stopword), '')
    return value


def suggestion_aliases(item):
    return [item.get('label', ''), *item.get('aliases', []), *item.get('terms', [])]


def resolve_query(query):
    original = query or ''
    normalized = normalize_query(original)
    typos = {clean(k): v for k, v in CONFIG.get('typos', {}).items()}
    corrected = typos.get(normalized, normalized)
    did_you_mean = corrected if corrected and corrected != normalized else ''
    matched = None
    for item in CONFIG.get('suggestions', []):
        labels = [clean(item.get('label', '')), *[clean(v) for v in item.get('aliases', []) if v]]
        terms = [clean(v) for v in item.get('terms', []) if v]
        # A short partial query such as「英文」must not accidentally select
        # the「英文免修」intent. Match exact labels/aliases or all intent terms.
        if any(value and value == corrected for value in labels):
            matched = item
            break
        if terms and all(term in corrected for term in terms):
            matched = item
            break
        if labels and any(value and value in corrected and len(value) >= 4 for value in labels):
            matched = item
            break

    groups = []
    if matched and matched.get('terms'):
        groups = [[term] for term in matched['terms']]
    else:
        for key, alternatives in CONFIG.get('synonyms', {}).items():
            key_clean = clean(key)
            if key_clean and key_clean in corrected:
                groups.append([key, *alternatives])
        if not groups:
            tokens = [token for token in re.split(r'\s+', corrected) if token]
            groups = [[token] for token in tokens] if tokens else []
            if not groups and corrected:
                groups = [[corrected]]

    intent = None
    if matched and matched.get('intent'):
        intent = {
            'label': matched.get('intent', matched.get('label', '')),
            'terms': matched.get('terms', []),
            'note': matched.get('note', ''),
        }
    return {
        'original': original,
        'normalized': normalized,
        'matched_query': corrected,
        'did_you_mean': did_you_mean,
        'groups': groups,
        'intent': intent,
    }


def row_to_item(row):
    (rowid, unit, subject, document_type, audience, keywords, published,
     updated, url, is_valid, pdf_pages, pdf_length, pdf_text, content) = row
    text = pdf_text or content or ''
    return {
        'id': rowid,
        'unit': unit or '未標示單位',
        'subject': subject or '未命名文件',
        'type': document_type or '未分類',
        'audience': audience or '未標示',
        'keywords': keywords or '',
        'published': published or '',
        'updated': updated or '',
        'url': url or '',
        'valid': validity(is_valid),
        'pdfPages': int(pdf_pages or 0),
        'pdfLength': int(pdf_length or 0),
        'snippet': re.sub(r'\s+', ' ', text).strip()[:260],
    }


def ensure_log_table(db):
    db.execute('''
        CREATE TABLE IF NOT EXISTS search_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            matched_query TEXT,
            filters_json TEXT,
            result_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    ''')


def log_search(db, query, matched_query, params, total):
    if not query.strip():
        return
    try:
        ensure_log_table(db)
        db.execute(
            'INSERT INTO search_logs(query, matched_query, filters_json, result_count, created_at) VALUES (?, ?, ?, ?, ?)',
            (query, matched_query, json.dumps(params, ensure_ascii=False), total,
             datetime.now(timezone.utc).isoformat()),
        )
        db.commit()
    except sqlite3.Error:
        db.rollback()


def fts_candidates(db, groups):
    if not groups:
        return None
    sets = []
    for group in groups:
        ids = set()
        for term in group:
            value = str(term).replace('"', ' ').strip()
            if not value:
                continue
            try:
                ids.update(row[0] for row in db.execute(
                    'SELECT rowid FROM items_fts WHERE items_fts MATCH ? LIMIT 5000', (value,)
                ).fetchall())
            except sqlite3.Error:
                continue
        if not ids:
            return []
        sets.append(ids)
    return sorted(set.intersection(*sets)) if sets else []


def item_score(item, resolved):
    query = clean(resolved.get('matched_query', ''))
    title = clean(item.get('subject', ''))
    text = clean(' '.join(str(item.get(key, '')) for key in ('subject', 'keywords', 'unit', 'audience', 'snippet')))
    score = 0
    if query and query in title:
        score += 60
    for group in resolved.get('groups', []):
        if any(clean(term) and clean(term) in title for term in group):
            score += 20
        elif any(clean(term) and clean(term) in text for term in group):
            score += 5
    for boost in CONFIG.get('boosts', []):
        queries = [clean(v) for v in boost.get('queries', [])]
        title_contains = clean(boost.get('title_contains', ''))
        if any(q and q in query for q in queries) and title_contains and title_contains in title:
            score += int(boost.get('boost', 100))
    return score


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/health':
            return self.send_json({'ok': True, 'engine': 'SQLite FTS5', 'database': str(DB_PATH.name), 'config': str(CONFIG_PATH.name)})
        if path == '/api/meta':
            return self.meta()
        if path == '/api/suggest':
            return self.suggest(parse_qs(urlparse(self.path).query))
        if path == '/api/search':
            return self.search(parse_qs(urlparse(self.path).query))
        return super().do_GET()

    def meta(self):
        with sqlite3.connect(DB_PATH) as db:
            count = db.execute('SELECT COUNT(*) FROM items').fetchone()[0]
            pdf = db.execute("SELECT COUNT(*) FROM items WHERE COALESCE(pdf_text,'') <> ''").fetchone()[0]
            units = [r[0] for r in db.execute("SELECT DISTINCT COALESCE(NULLIF(unit,''),'未標示單位') FROM items ORDER BY 1")]
            types = [r[0] for r in db.execute("SELECT DISTINCT COALESCE(NULLIF(document_type,''),'未分類') FROM items ORDER BY 1")]
        return self.send_json({
            'count': count, 'pdfCount': pdf, 'units': units, 'types': types,
            'suggestionCount': len(CONFIG.get('suggestions', [])), 'engine': 'SQLite FTS5',
        })

    def suggest(self, params):
        query = normalize_query(params.get('q', [''])[0])
        suggestions = []
        for item in CONFIG.get('suggestions', []):
            aliases = [v for v in suggestion_aliases(item) if v]
            if not query or any(query in clean(alias) for alias in aliases):
                suggestions.append({
                    'label': item.get('label', ''),
                    'terms': item.get('terms', []),
                    'note': item.get('note', ''),
                })
        return self.send_json({'items': suggestions[:8]})

    def search(self, params):
        q = params.get('q', [''])[0]
        topic = params.get('topic', [''])[0]
        unit = params.get('unit', ['all'])[0]
        doc_type = params.get('type', ['all'])[0]
        valid = params.get('valid', ['all'])[0]
        sort = params.get('sort', ['desc'])[0]
        resolved = resolve_query(q)
        has_query = bool(resolved['matched_query'])
        clauses, args = [], []
        if unit != 'all':
            clauses.append('i.unit = ?'); args.append(unit)
        if doc_type != 'all':
            clauses.append('i.document_type = ?'); args.append(doc_type)
        if valid != 'all':
            clauses.append("CASE WHEN i.is_valid = 1 THEN 'yes' WHEN i.is_valid = 0 THEN 'no' ELSE 'unknown' END = ?"); args.append(valid)
        if topic:
            clauses.append("(i.subject LIKE ? OR i.keywords LIKE ? OR i.unit LIKE ? OR i.pdf_text LIKE ? OR i.content LIKE ?)")
            args.extend([f'%{topic}%'] * 5)

        with sqlite3.connect(DB_PATH) as db:
            db.row_factory = sqlite3.Row
            base = ' FROM items i '
            search_args = []
            candidates = fts_candidates(db, resolved['groups']) if has_query else None
            if has_query and candidates:
                search_clause = 'JOIN items_fts f ON f.rowid = i.rowid WHERE f.rowid IN (' + ','.join('?' for _ in candidates) + ')'
                search_args = candidates
            elif has_query:
                terms = [term for group in resolved['groups'] for term in group[:1]] or [resolved['matched_query']]
                search_clause = ' WHERE ' + ' AND '.join(
                    '(i.subject LIKE ? OR i.title LIKE ? OR i.keywords LIKE ? OR i.unit LIKE ? OR i.pdf_text LIKE ? OR i.content LIKE ?)' for _ in terms
                )
                for term in terms:
                    search_args.extend([f'%{term}%'] * 6)
            else:
                search_clause = ''
            if search_clause:
                search_sql = search_clause + (' AND ' + ' AND '.join(clauses) if clauses else '')
            else:
                search_sql = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
            date_order = 'ASC' if sort == 'asc' else 'DESC'
            sql = (
                "SELECT i.rowid,i.unit,i.subject,i.document_type,i.applicable_audience,i.keywords,"
                "i.published_date,i.last_updated_date,i.original_url,i.is_valid,i.pdf_pages,"
                "length(COALESCE(i.pdf_text,i.content,'')),i.pdf_text,i.content" + base + search_sql +
                f" ORDER BY COALESCE(NULLIF(i.last_updated_date,''),i.published_date,'') {date_order}, i.rowid DESC LIMIT 400"
            )
            rows = db.execute(sql, search_args + args).fetchall()
            items = [row_to_item(tuple(r)) for r in rows]
            total = db.execute('SELECT COUNT(*)' + base + search_sql, search_args + args).fetchone()[0]
            log_search(db, q, resolved['matched_query'], params, total)

        if has_query:
            items.sort(key=lambda item: item_score(item, resolved), reverse=True)
        items = items[:120]
        return self.send_json({
            'items': items,
            'total': total,
            'intent': resolved['intent'],
            'didYouMean': resolved['did_you_mean'],
            'matchedQuery': resolved['matched_query'],
            'engine': 'SQLite FTS5',
        })


if __name__ == '__main__':
    print(f'Campus Atlas server running at http://127.0.0.1:{PORT}')
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()

