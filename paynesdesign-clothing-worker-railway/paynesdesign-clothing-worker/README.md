# PaynesDesign FiveM Clothing Worker

Docker-based queue worker for the existing Supabase `clothing_conversion_jobs` table and private `paynes-clothing-engine` bucket.

## Current implementation status

- Polls the Supabase queue and conditionally claims one queued job.
- Downloads source files from the private bucket using a server-side service-role key.
- Implements safe ZIP inspection (path traversal and expanded-size checks).
- Completes `inspect` jobs with file inventory and detected extensions.
- Marks unsupported conversion jobs as failed with an explicit message. It does **not** pretend to convert YDD/YTD assets.

Actual YDD/YTD import, texture editing, Sollumz export, metadata generation and FiveM resource packaging are not implemented in this initial worker. Those require a tested Blender/Sollumz/CodeWalker-compatible pipeline and representative sample assets.

## Railway deployment

1. Create a Railway project and deploy this folder from a GitHub repository, or use Railway's Dockerfile deployment flow.
2. Add these Railway Variables (never put the service-role key in the browser/dashboard):
   - `SUPABASE_URL` = `https://yzwnxkbhbiomaukqvrdt.supabase.co`
   - `SUPABASE_SERVICE_ROLE_KEY` = the project's service-role secret, copied from Supabase API settings
   - `STORAGE_BUCKET` = `paynes-clothing-engine`
   - `POLL_SECONDS` = `5`
3. Deploy. Railway should detect the Dockerfile and run `python worker.py` as a long-running worker process.
4. Check logs for `PaynesDesign clothing worker starting`.

Do not commit `.env` files or service-role keys. Rotate the key if it is ever exposed.

## Creating an inspection job

Upload a source file to the private bucket, then insert a row into `public.clothing_conversion_jobs` using the authenticated dashboard session. Set `source_path` to the exact object path and `job_type` to `inspect`. The worker will populate `result`, `progress`, `stage` and `status`.

## Safety and operational notes

- Keep the bucket private.
- Restrict the Railway service to trusted staff and keep secrets in Railway Variables.
- Set sensible upload limits in the dashboard as well as the worker.
- This worker does not execute uploaded files.
