import io, json, logging, os, time, uuid, zipfile
from pathlib import PurePosixPath
import requests

logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO'), format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('paynes-clothing-worker')
SUPABASE_URL = os.environ['SUPABASE_URL'].rstrip('/')
SERVICE_KEY = os.environ['SUPABASE_SERVICE_ROLE_KEY']
BUCKET = os.getenv('STORAGE_BUCKET', 'paynes-clothing-engine')
POLL_SECONDS = int(os.getenv('POLL_SECONDS', '5'))
WORKER_ID = os.getenv('WORKER_ID', f'railway-{uuid.uuid4().hex[:8]}')
MAX_DOWNLOAD = int(os.getenv('MAX_DOWNLOAD_MB', '1024')) * 1024 * 1024
HEADERS = {'apikey': SERVICE_KEY, 'Authorization': f'Bearer {SERVICE_KEY}', 'Content-Type': 'application/json'}

class JobError(Exception):
    pass

def api(method, path, **kwargs):
    r = requests.request(method, f'{SUPABASE_URL}/rest/v1/{path}', headers={**HEADERS, **kwargs.pop('headers', {})}, timeout=60, **kwargs)
    if not r.ok:
        raise JobError(f'Supabase API error {r.status_code}: {r.text[:500]}')
    return r

def storage_download(path):
    safe = '/'.join(requests.utils.quote(p, safe='') for p in path.split('/'))
    r = requests.get(f'{SUPABASE_URL}/storage/v1/object/{BUCKET}/{safe}', headers=HEADERS, timeout=120, stream=True)
    if not r.ok:
        raise JobError(f'Could not download source file ({r.status_code}).')
    data = bytearray()
    for chunk in r.iter_content(1024 * 1024):
        data.extend(chunk)
        if len(data) > MAX_DOWNLOAD:
            raise JobError('Source file exceeds the configured download limit.')
    return bytes(data)

def storage_upload(path, data, content_type='application/zip'):
    safe = '/'.join(requests.utils.quote(p, safe='') for p in path.split('/'))
    h = {**HEADERS, 'Content-Type': content_type, 'x-upsert': 'true'}
    r = requests.post(f'{SUPABASE_URL}/storage/v1/object/{BUCKET}/{safe}', headers=h, data=data, timeout=180)
    if not r.ok:
        raise JobError(f'Could not upload output ({r.status_code}): {r.text[:300]}')

def update_job(job_id, **fields):
    fields['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    api('PATCH', f"clothing_conversion_jobs?id=eq.{job_id}", json=fields, headers={'Prefer':'return=minimal'})

def safe_zip_report(data):
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return None
    entries=[]
    total=0
    for info in z.infolist():
        p=PurePosixPath(info.filename)
        if p.is_absolute() or '..' in p.parts or info.filename.startswith(('\\','/')):
            raise JobError('Archive contains an unsafe file path.')
        if info.file_size > MAX_DOWNLOAD:
            raise JobError('Archive contains a file exceeding the configured size limit.')
        total += info.file_size
        if total > MAX_DOWNLOAD:
            raise JobError('Uncompressed archive size exceeds the configured limit.')
        if not info.is_dir():
            entries.append({'path':info.filename,'size':info.file_size,'extension':p.suffix.lower()})
    return {'archive':True,'file_count':len(entries),'uncompressed_bytes':total,'files':entries[:500]}

def inspect_source(job, data):
    report = safe_zip_report(data)
    if report is None:
        name = (job.get('original_filename') or '').lower()
        ext = PurePosixPath(name).suffix
        report = {'archive':False,'file_count':1,'files':[{'path':job.get('original_filename') or 'source','size':len(data),'extension':ext}]}
    exts = sorted({f['extension'] for f in report['files'] if f['extension']})
    report['detected_extensions'] = exts
    report['fivem_assets_detected'] = any(x in exts for x in ['.ydd','.ytd','.ymt','.yft','.ypt'])
    report['conversion_note'] = 'Inspection only. This worker does not yet import or rebuild YDD/YTD assets.'
    return report

def process(job):
    jid = job['id']
    update_job(jid, status='validating', progress=5, stage='Downloading source', worker_id=WORKER_ID, error_message=None)
    if not job.get('source_path'):
        raise JobError('Job has no source_path.')
    data = storage_download(job['source_path'])
    update_job(jid, status='processing', progress=35, stage='Inspecting files')
    kind = job.get('job_type')
    if kind == 'inspect':
        result = inspect_source(job, data)
        update_job(jid, status='completed', progress=100, stage='Inspection complete', result=result, completed_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
        return
    raise JobError(f"Job type '{kind}' is queued in the database but its conversion adapter is not installed. Only 'inspect' is implemented in this worker build; YDD/YTD conversion must not be reported as completed.")

def claim_one():
    r=api('GET', 'clothing_conversion_jobs?status=eq.queued&order=created_at.asc&limit=1&select=*')
    rows=r.json()
    if not rows: return None
    job=rows[0]
    # Conditional update prevents two workers claiming the same queued job.
    r=api('PATCH', f"clothing_conversion_jobs?id=eq.{job['id']}&status=eq.queued", json={'status':'validating','worker_id':WORKER_ID,'stage':'Claimed by worker','progress':1}, headers={'Prefer':'return=representation'})
    claimed=r.json()
    return job if claimed else None

def main():
    log.info('PaynesDesign clothing worker starting: %s', WORKER_ID)
    while True:
        try:
            job=claim_one()
            if not job:
                time.sleep(POLL_SECONDS); continue
            log.info('Processing job %s (%s)', job['id'], job.get('job_type'))
            try: process(job)
            except Exception as e:
                log.exception('Job failed: %s', job['id'])
                try: update_job(job['id'], status='failed', progress=100, stage='Failed', error_message=str(e)[:1500], completed_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
                except Exception: log.exception('Could not mark job failed')
        except Exception:
            log.exception('Worker loop error')
            time.sleep(POLL_SECONDS)

if __name__ == '__main__': main()
