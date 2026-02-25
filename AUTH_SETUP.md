# Fix 401 Authentication Failed

If you get `401 Unauthorized` when using Quick Book or other API calls:

## Set SUPABASE_JWT_SECRET (Required for reliable auth)

1. Go to [Supabase Dashboard](https://supabase.com/dashboard) → select your project
2. **Project Settings** (gear icon) → **API** → scroll to **JWT Settings**
   - Or go directly: `https://supabase.com/dashboard/project/YOUR_PROJECT_REF/settings/api`
3. Find **JWT Secret** (sometimes labeled "Legacy JWT Secret" or under "JWT Keys")
4. Click **Reveal** and copy the secret
5. Add to `python-backend/.env`:
   ```
   SUPABASE_JWT_SECRET=your-copied-secret-here
   ```
6. Restart the Python backend: `cd python-backend && python -m uvicorn app.main:app --reload --port 8000`

## Backend logs

When auth fails, logs show: `Supabase API auth failed: 401 - {...}`  
When auth succeeds: `Supabase API token verified for user: <uuid>`
