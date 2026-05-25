-- Full LLM tool-calling thread per chat session.
--
-- chat_messages stores the human-facing conversation (user prompts + assistant
-- summaries) for rendering. This table stores the RAW agent thread — including
-- assistant tool_calls and tool-result observations — so a follow-up turn
-- continues the ReAct loop with everything the agent already did in the session
-- instead of re-exploring from scratch.

CREATE TABLE IF NOT EXISTS agent_messages (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   uuid NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    seq          bigint NOT NULL,
    role         text NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
    content      text,
    tool_calls   jsonb,          -- assistant: list of {id, type, function:{name, arguments}}
    tool_call_id text,           -- tool: which call this observation answers
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_messages_session
    ON agent_messages (session_id, seq);
