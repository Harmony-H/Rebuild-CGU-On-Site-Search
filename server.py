from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from pathlib import Path
import json, re, sqlite3

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / '04_爬蟲資料' / 'cgu_data' / 'cgu.db'
PORT = 8765

def clean(value):
    return re.sub(r'[\s，、,.。／/]+', '', (value or '').lower())

def validity(value):
    return 'yes' if value == 1 else 'no' if value == 0 else 'unknown'

def intent_for(query):
    q = clean(query)
    if '英文' in q and '免修' in q:
        return {
            'label': '英文免修',
            'terms': ['英文', '免修'],
            'note': '已將查詢對應到英文課程免修相關文件。',
        }
    return None

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
            return self.send_json({'ok': True, 'engine': 'SQLite FTS5', 'database': str(DB_PATH.name)})
        if path == '/api/meta':
            return self.meta()
        if path == '/api/search':
            return self.search(parse_qs(urlparse(self.path).query))
        return super().do_GET()

    def meta(self):
        with sqlite3.connect(DB_PATH) as db:
            count = db.execute('SELECT COUNT(*) FROM items').fetchone()[0]
            pdf = db.execute("SELECT COUNT(*) FROM items WHERE COALESCE(pdf_text,'') <> ''").fetchone()[0]
            units = [r[0] for r in db.execute("SELECT DISTINCT COALESCE(NULLIF(unit,''),'未標示單位') FROM items ORDER BY 1")]
            types = [r[0] for r in db.execute("SELECT DISTINCT COALESCE(NULLIF(document_type,''),'未分類') FROM items ORDER BY 1")]
        return self.send_json({'count': count, 'pdfCount': pdf, 'units': units, 'types': types, 'engine': 'SQLite FTS5'})

    def search(self, params):
        q = params.get('q', [''])[0]
        topic = params.get('topic', [''])[0]
        unit = params.get('unit', ['all'])[0]
        doc_type = params.get('type', ['all'])[0]
        valid = params.get('valid', ['all'])[0]
        sort = params.get('sort', ['desc'])[0]
        intent = intent_for(q)
        clauses, args = [], []
        if unit != 'all': clauses.append('i.unit = ?'); args.append(unit)
        if doc_type != 'all': clauses.append('i.document_type = ?'); args.append(doc_type)
        if valid != 'all':
            clauses.append('CASE WHEN i.is_valid = 1 THEN \'yes\' WHEN i.is_valid = 0 THEN \'no\' ELSE \'unknown\' END = ?'); args.append(valid)
        if topic:
            clauses.append("(i.subject LIKE ? OR i.keywords LIKE ? OR i.unit LIKE ? OR i.pdf_text LIKE ? OR i.content LIKE ?)")
            args.extend([f'%{topic}%'] * 5)

        with sqlite3.connect(DB_PATH) as db:
            db.row_factory = sqlite3.Row
            base = ' FROM items i '
            search_clause = ''
            search_args = []
            if q:
                # FTS5 is the first path. If a short Chinese query or a non-contiguous phrase
                # returns no rows, the LIKE fallback keeps the index useful without guessing.
                match_query = q.replace('"', ' ').strip()
                try:
                    candidate = db.execute('SELECT rowid FROM items_fts WHERE items_fts MATCH ? LIMIT 5000', (match_query,)).fetchall()
                except sqlite3.Error:
                    candidate = []
                if candidate:
                    search_clause = 'JOIN items_fts f ON f.rowid = i.rowid'
                    search_clause += ' WHERE f.rowid IN (' + ','.join('?' for _ in candidate) + ')'
                    search_args = [r[0] for r in candidate]
                else:
                    terms = intent['terms'] if intent else [q]
                    search_clause = ' WHERE ' + ' AND '.join('(i.subject LIKE ? OR i.title LIKE ? OR i.keywords LIKE ? OR i.unit LIKE ? OR i.pdf_text LIKE ? OR i.content LIKE ?)' for _ in terms)
                    for term in terms: search_args.extend([f'%{term}%'] * 6)
            where = list(clauses)
            if search_clause:
                if ' WHERE ' in search_clause:
                    search_sql = search_clause + (' AND ' + ' AND '.join(where) if where else '')
                else:
                    search_sql = search_clause + (' WHERE ' + ' AND '.join(where) if where else '')
            else:
                search_sql = (' WHERE ' + ' AND '.join(where)) if where else ''
            order = 'ASC' if sort == 'asc' else 'DESC'
            sql = 'SELECT i.rowid,i.unit,i.subject,i.document_type,i.applicable_audience,i.keywords,i.published_date,i.last_updated_date,i.original_url,i.is_valid,i.pdf_pages,length(COALESCE(i.pdf_text,i.content,\'\')),i.pdf_text,i.content' + base + search_sql + f' ORDER BY COALESCE(NULLIF(i.last_updated_date,\'\'),i.published_date,\'\') {order}, i.rowid DESC LIMIT 120'
            rows = db.execute(sql, search_args + args).fetchall()
            items = [row_to_item(tuple(r)) for r in rows]
            count_sql = 'SELECT COUNT(*)' + base + search_sql
            total = db.execute(count_sql, search_args + args).fetchone()[0]
        return self.send_json({'items': items, 'total': total, 'intent': intent, 'engine': 'SQLite FTS5'})

if __name__ == '__main__':
    print(f'Campus Atlas server running at http://127.0.0.1:{PORT}')
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
