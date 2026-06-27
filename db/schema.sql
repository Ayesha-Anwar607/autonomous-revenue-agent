-- ================================================================
-- Phase 3: Enterprise Revenue Recovery Agent — Database Schema
-- Run this to bootstrap tables inside your Postgres container.
-- ================================================================

-- ----------------------------------------------------------------
-- TABLE: sessions
-- Stores every agent conversation — query + response pairs.
-- This gives the agent its long-term conversational memory.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sessions (
    id          SERIAL PRIMARY KEY,
    session_id  VARCHAR(128)  NOT NULL,
    user_id     VARCHAR(128)  NOT NULL,
    query       TEXT          NOT NULL,
    response    TEXT          NOT NULL,
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- Index for fast lookup of a user's recent session history
CREATE INDEX IF NOT EXISTS idx_sessions_session_id ON sessions (session_id, created_at DESC);

-- ----------------------------------------------------------------
-- TABLE: revenue_alerts
-- Stores every detected revenue leakage flagged by the agent.
-- Status lifecycle: open → resolved / dismissed
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS revenue_alerts (
    id                  SERIAL PRIMARY KEY,
    alert_type          VARCHAR(64)     NOT NULL,  -- 'stalled_deal' | 'churn_risk' | 'overdue_invoice'
    customer_name       VARCHAR(256)    NOT NULL,
    financial_impact    NUMERIC(15, 2)  NOT NULL,  -- Dollar value at risk
    reasoning           TEXT            NOT NULL,  -- Explainable AI rationale
    recommended_action  TEXT,
    status              VARCHAR(32)     NOT NULL DEFAULT 'open',  -- 'open' | 'resolved' | 'dismissed'
    detected_at         TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    resolved_at         TIMESTAMPTZ     -- NULL until resolved
);

-- Index for querying only open alerts sorted by financial impact
CREATE INDEX IF NOT EXISTS idx_alerts_status_impact
    ON revenue_alerts (status, financial_impact DESC);
