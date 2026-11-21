CREATE TABLE IF NOT EXISTS public.user_profiles (
    user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    display_name TEXT NOT NULL DEFAULT 'Default User',
    default_template_id UUID NULL,
    preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.rule_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    is_system_default BOOLEAN NOT NULL DEFAULT false,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    schema_version TEXT NOT NULL DEFAULT '1.0',
    rules_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_rule_templates_user ON public.rule_templates(user_id, is_system_default);
CREATE TABLE IF NOT EXISTS public.reimbursement_batches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    template_id UUID NOT NULL REFERENCES public.rule_templates(id),
    expense_type TEXT NOT NULL,
    title TEXT NOT NULL,
    batch_date DATE NOT NULL,
    trip_start_date DATE NULL,
    trip_end_date DATE NULL,
    destinations JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'draft',
    missing_alerts JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_batches_user_date ON public.reimbursement_batches(user_id, batch_date DESC);
CREATE TABLE IF NOT EXISTS public.raw_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    batch_id UUID NOT NULL REFERENCES public.reimbursement_batches(id) ON DELETE CASCADE,
    original_filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    file_size_bytes BIGINT NOT NULL,
    sha256_hash TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    doc_role TEXT NOT NULL DEFAULT 'unknown',
    ocr_status TEXT NOT NULL DEFAULT 'pending',
    extracted_json JSONB NULL,
    quality_score REAL NULL,
    duplicate_of_id UUID NULL REFERENCES public.raw_documents(id) ON DELETE SET NULL,
    exif_taken_at TIMESTAMPTZ NULL,
    client_ocr_json JSONB NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_docs_batch_hash ON public.raw_documents(batch_id, sha256_hash);
CREATE INDEX IF NOT EXISTS idx_raw_docs_user_batch ON public.raw_documents(user_id, batch_id);
CREATE TABLE IF NOT EXISTS public.transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    batch_id UUID NOT NULL REFERENCES public.reimbursement_batches(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    region_scope TEXT NOT NULL,
    region_detail TEXT NOT NULL,
    tx_date DATE NOT NULL,
    merchant TEXT NOT NULL,
    currency TEXT NOT NULL,
    original_amount NUMERIC(12, 2) NOT NULL,
    tip_amount NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    split_count INT NOT NULL DEFAULT 1,
    claim_amount NUMERIC(12, 2) NOT NULL,
    airport_direction TEXT NULL,
    airport_iata TEXT NULL,
    payment_status TEXT NOT NULL,
    flags JSONB NOT NULL DEFAULT '[]'::jsonb,
    notes TEXT NOT NULL DEFAULT '',
    is_user_verified BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_transactions_user_batch ON public.transactions(user_id, batch_id);
CREATE TABLE IF NOT EXISTS public.transaction_documents (
    transaction_id UUID NOT NULL REFERENCES public.transactions(id) ON DELETE CASCADE,
    raw_document_id UUID NOT NULL REFERENCES public.raw_documents(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    link_role TEXT NOT NULL,
    export_filename TEXT NULL,
    PRIMARY KEY (transaction_id, raw_document_id)
);
CREATE TABLE IF NOT EXISTS public.jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    batch_id UUID NOT NULL REFERENCES public.reimbursement_batches(id) ON DELETE CASCADE,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    progress INT NOT NULL DEFAULT 0,
    result_storage_key TEXT NULL,
    error_message TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_jobs_user_batch ON public.jobs(user_id, batch_id, created_at DESC);
ALTER TABLE public.user_profiles ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS user_profiles_owner_only ON public.user_profiles;
CREATE POLICY user_profiles_owner_only ON public.user_profiles
    FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
ALTER TABLE public.rule_templates ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS templates_read ON public.rule_templates;
CREATE POLICY templates_read ON public.rule_templates
    FOR SELECT USING (user_id IS NULL OR auth.uid() = user_id);
DROP POLICY IF EXISTS templates_write ON public.rule_templates;
CREATE POLICY templates_write ON public.rule_templates
    FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
ALTER TABLE public.reimbursement_batches ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS reimbursement_batches_owner_only ON public.reimbursement_batches;
CREATE POLICY reimbursement_batches_owner_only ON public.reimbursement_batches
    FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
ALTER TABLE public.raw_documents ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS raw_documents_owner_only ON public.raw_documents;
CREATE POLICY raw_documents_owner_only ON public.raw_documents
    FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
ALTER TABLE public.transactions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS transactions_owner_only ON public.transactions;
CREATE POLICY transactions_owner_only ON public.transactions
    FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
ALTER TABLE public.transaction_documents ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS transaction_documents_owner_only ON public.transaction_documents;
CREATE POLICY transaction_documents_owner_only ON public.transaction_documents
    FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
ALTER TABLE public.jobs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS jobs_owner_only ON public.jobs;
CREATE POLICY jobs_owner_only ON public.jobs
    FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
