"""Premium dashboard styling."""

PREMIUM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700;1,9..40,400&display=swap');

html, body, [class*="css"] {
    font-family: 'DM Sans', system-ui, sans-serif;
}

.stApp {
    background: linear-gradient(165deg, #0a0e17 0%, #111827 45%, #0f172a 100%);
}

[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0f172a 0%, #1e293b 100%);
    border-right: 1px solid rgba(148, 163, 184, 0.12);
}

[data-testid="stSidebar"] .stRadio label {
    padding: 0.55rem 0.75rem;
    border-radius: 8px;
    margin: 2px 0;
}

.premium-badge {
    display: inline-block;
    background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%);
    color: #0f172a;
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 0.25rem 0.6rem;
    border-radius: 4px;
    margin-bottom: 0.5rem;
}

.free-badge {
    display: inline-block;
    background: rgba(34, 211, 238, 0.12);
    color: #67e8f9;
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 0.25rem 0.6rem;
    border-radius: 4px;
    margin-bottom: 0.5rem;
    border: 1px solid rgba(34, 211, 238, 0.35);
}

.version-chip {
    display: inline-block;
    color: #94a3b8;
    font-size: 0.75rem;
    margin-left: 0.35rem;
}

.asset-pill {
    display: inline-block;
    background: rgba(59, 130, 246, 0.15);
    color: #93c5fd;
    border: 1px solid rgba(59, 130, 246, 0.3);
    font-size: 0.72rem;
    font-weight: 600;
    padding: 0.2rem 0.55rem;
    border-radius: 999px;
    margin: 0 0.25rem 0.25rem 0;
}

.log-meta {
    color: #64748b;
    font-size: 0.8rem;
    margin: 0.35rem 0 0.75rem 0;
}

.log-empty {
    background: rgba(30, 41, 59, 0.45);
    border: 1px dashed rgba(148, 163, 184, 0.25);
    border-radius: 12px;
    padding: 1.25rem 1.5rem;
    color: #94a3b8;
    text-align: center;
}

.upgrade-box {
    background: linear-gradient(135deg, rgba(245, 158, 11, 0.12) 0%, rgba(59, 130, 246, 0.08) 100%);
    border: 1px solid rgba(245, 158, 11, 0.35);
    border-radius: 12px;
    padding: 1.25rem 1.5rem;
    margin: 1rem 0;
}

.upgrade-box h3 {
    color: #fbbf24;
    margin: 0 0 0.5rem 0;
    font-size: 1.1rem;
}

.upgrade-box p {
    color: #94a3b8;
    margin: 0 0 0.75rem 0;
}

.premium-hero {
    background: linear-gradient(135deg, rgba(59, 130, 246, 0.15) 0%, rgba(245, 158, 11, 0.08) 100%);
    border: 1px solid rgba(148, 163, 184, 0.15);
    border-radius: 16px;
    padding: 1.5rem 1.75rem;
    margin-bottom: 1.5rem;
}

.premium-hero h1 {
    font-size: 1.75rem;
    font-weight: 700;
    color: #f8fafc;
    margin: 0 0 0.35rem 0;
}

.premium-hero p {
    color: #94a3b8;
    margin: 0;
    font-size: 0.95rem;
}

.metric-card {
    background: rgba(30, 41, 59, 0.6);
    border: 1px solid rgba(148, 163, 184, 0.12);
    border-radius: 12px;
    padding: 1rem 1.25rem;
    text-align: center;
}

.metric-card .label {
    color: #94a3b8;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 0.25rem;
}

.metric-card .value {
    color: #f8fafc;
    font-size: 1.75rem;
    font-weight: 700;
}

.metric-card .value.win { color: #34d399; }
.metric-card .value.loss { color: #f87171; }
.metric-card .value.gold { color: #fbbf24; }

.status-pill {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.35rem 0.85rem;
    border-radius: 999px;
    font-size: 0.8rem;
    font-weight: 600;
}

.status-pill.running {
    background: rgba(52, 211, 153, 0.15);
    color: #34d399;
    border: 1px solid rgba(52, 211, 153, 0.35);
}

.status-pill.stopped {
    background: rgba(148, 163, 184, 0.12);
    color: #94a3b8;
    border: 1px solid rgba(148, 163, 184, 0.2);
}

.status-pill.live {
    background: rgba(248, 113, 113, 0.15);
    color: #f87171;
    border: 1px solid rgba(248, 113, 113, 0.35);
}

div[data-testid="stMetric"] {
    background: rgba(30, 41, 59, 0.5);
    border: 1px solid rgba(148, 163, 184, 0.1);
    border-radius: 12px;
    padding: 0.75rem 1rem;
}

div[data-testid="stMetric"] label {
    color: #94a3b8 !important;
}

div[data-testid="stMetric"] [data-testid="stMetricValue"] {
    color: #f8fafc !important;
}

.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
    border: none;
    font-weight: 600;
}

.stButton > button {
    border-radius: 10px;
    font-weight: 500;
}

[data-testid="stDeployButton"],
.stDeployButton,
.stAppDeployButton {
    display: none !important;
}

footer,
[data-testid="stFooter"],
.stApp footer,
.stApp > footer {
    display: none !important;
    visibility: hidden !important;
    height: 0 !important;
    overflow: hidden !important;
}

a[href*="streamlit.io"],
[data-testid="stToolbar"] a[href*="streamlit.io"] {
    display: none !important;
    visibility: hidden !important;
}
</style>
"""


def inject_premium_theme() -> None:
    import streamlit as st

    st.markdown(PREMIUM_CSS, unsafe_allow_html=True)
