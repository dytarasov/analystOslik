CREATE TABLE llm_calls (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id       uuid,
    task_id      uuid,
    step_name    text,
    prompt_name  text,
    model        text NOT NULL,
    request      jsonb,
    response     text,
    tokens_in    int,
    tokens_out   int,
    latency_ms   int,
    error        text,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX llm_calls_run_idx ON llm_calls(run_id);
CREATE INDEX llm_calls_task_idx ON llm_calls(task_id);
CREATE INDEX llm_calls_created_idx ON llm_calls(created_at DESC);
