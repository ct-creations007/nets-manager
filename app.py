"""Cricket Nets Manager - Streamlit + SQLAlchemy.
Runs on SQLite locally (zero setup) and Postgres/Supabase in production (set DATABASE_URL)."""
import hmac
import io
import os
from datetime import date

import altair as alt
import pandas as pd
import sqlalchemy as sa
import streamlit as st

CLUB_NAME = "Your Cricket Club"  # <- change to your club's name
GREEN = "#14532D"
_here = os.path.dirname(os.path.abspath(__file__))
LOGO = next((os.path.join(_here, "assets", f) for f in ("logo.png", "logo.jpg", "logo.jpeg", "logo.svg")
             if os.path.exists(os.path.join(_here, "assets", f))), None)

st.set_page_config(page_title=f"{CLUB_NAME} - Nets Manager", page_icon=LOGO or "🏏", layout="wide")
if LOGO:
    st.logo(LOGO)

CSS = """
.block-container { padding-top: 2rem; max-width: 1200px; }
h1, h2, h3 { color: #14532D; font-weight: 700; }
h2 { border-bottom: 3px solid #B3261E; padding-bottom: .3rem; margin-bottom: 1rem; }
[data-testid="stSidebar"] { background: linear-gradient(180deg, #0B3D20 0%, #14532D 100%); }
[data-testid="stSidebar"] * { color: #F3EFE0; }
[data-testid="stLogo"] { height: 3.2rem; }
[data-testid="stSidebarNavLink"] { border-radius: 10px; margin: 2px 6px; padding: .5rem .8rem; }
[data-testid="stSidebarNavLink"]:hover { background: rgba(255,255,255,.14); }
[data-testid="stSidebarNavLink"][aria-current="page"] { background: #B3261E; font-weight: 600; }
[data-testid="stMetric"] { background: #FFFFFF; border: 1px solid #E3DCC8; border-left: 6px solid #14532D;
    border-radius: 12px; padding: 14px 16px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
[data-testid="stMetricLabel"] p { color: #5B6B61; font-weight: 600; }
[data-testid="stMetricValue"] { color: #14532D; }
.stButton > button, .stDownloadButton > button { border-radius: 8px; font-weight: 600; }
[data-testid="stDataFrame"] { border: 1px solid #E3DCC8; border-radius: 10px; overflow: hidden; }
footer { visibility: hidden; }
"""
st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)

# "selected": fee is due from every selected player; "attended": only from those who attended
EXPECTED_BASIS = "selected"


def secret(key, default=None):
    try:
        return st.secrets[key]
    except (KeyError, FileNotFoundError):
        return os.environ.get(key, default)


# ---------------- password gate ----------------
PW = secret("APP_PASSWORD")
if PW and not st.session_state.get("ok"):
    if LOGO:
        st.image(LOGO, width=150)
    st.title(CLUB_NAME)
    st.caption("Nets Manager - sign in to continue")
    entered = st.text_input("Password", type="password")
    if entered and hmac.compare_digest(entered, str(PW)):
        st.session_state.ok = True
        st.rerun()
    elif entered:
        st.error("Wrong password")
    st.stop()

# ---------------- database ----------------
C = sa.Column
md = sa.MetaData()
teams = sa.Table("teams", md, C("id", sa.Integer, primary_key=True),
                 C("name", sa.String(100), nullable=False, unique=True))
players = sa.Table("players", md, C("id", sa.Integer, primary_key=True),
                   C("team_id", sa.ForeignKey("teams.id"), nullable=False),
                   C("name", sa.String(100), nullable=False), C("contact", sa.String(100), default=""),
                   C("active", sa.Boolean, default=True), sa.UniqueConstraint("team_id", "name"))
sessions = sa.Table("sessions", md, C("id", sa.Integer, primary_key=True),
                    C("team_id", sa.ForeignKey("teams.id"), nullable=False),
                    C("session_date", sa.Date, nullable=False),
                    C("venue", sa.String(100), nullable=False, default=""),
                    C("fee", sa.Float, default=0), C("notes", sa.Text, default=""),
                    sa.UniqueConstraint("team_id", "session_date", "venue"))
sp = sa.Table("session_players", md, C("id", sa.Integer, primary_key=True),
              C("session_id", sa.ForeignKey("sessions.id"), nullable=False),
              C("player_id", sa.ForeignKey("players.id"), nullable=False),
              C("attended", sa.Boolean, nullable=False, default=False),
              C("paid", sa.Boolean, nullable=False, default=False),
              C("amount_paid", sa.Float, default=0), C("paid_date", sa.Date),
              sa.UniqueConstraint("session_id", "player_id"))


@st.cache_resource
def get_engine():
    e = sa.create_engine(secret("DATABASE_URL"), pool_pre_ping=True, pool_recycle=300)
    md.create_all(e)
    if e.dialect.name == "postgresql":  # lock the tables away from Supabase's public API
        with e.begin() as c:
            for t in md.tables:
                c.execute(sa.text(f'ALTER TABLE "{t}" ENABLE ROW LEVEL SECURITY'))
    return e


if not secret("DATABASE_URL"):
    st.error("DATABASE_URL is not configured, so data would not be saved. "
             "Add it to the app's Secrets (cloud) or .streamlit/secrets.toml (local).")
    st.stop()
eng = get_engine()


def q(sql, **p):
    with eng.connect() as c:
        return pd.read_sql(sa.text(sql), c, params=p)


def run(stmt):
    with eng.begin() as c:
        return c.execute(stmt)


def clean(v):
    return "" if pd.isna(v) else str(v).strip()


def to_date(v):
    return None if pd.isna(v) else pd.to_datetime(v).date()


def pick_team(key):
    t = q("select id, name from teams order by name")
    if t.empty:
        st.info("Create a team first (Teams & Players page).")
        st.stop()
    name = st.selectbox("Team", t.name, key=key)
    return int(t.loc[t.name == name, "id"].iloc[0])


def pick_session(tid, key):
    s = q("select id, session_date, venue, fee, notes from sessions where team_id=:t "
          "order by session_date desc", t=tid)
    if s.empty:
        return None
    s["label"] = pd.to_datetime(s.session_date).dt.strftime("%d %b %Y") + " " + s.venue.fillna("")
    lab = st.selectbox("Session", s.label, key=key)
    return s[s.label == lab].iloc[0]


def all_rows():
    df = q("""select sp.id, sp.session_id, s.team_id, t.name as team, s.session_date, s.venue, s.fee,
              sp.player_id, p.name as player, sp.attended, sp.paid, sp.amount_paid, sp.paid_date
              from session_players sp join sessions s on s.id = sp.session_id
              join teams t on t.id = s.team_id join players p on p.id = sp.player_id""")
    df["session_date"] = pd.to_datetime(df.session_date)
    for c in ("attended", "paid"):
        df[c] = df[c].fillna(False).astype(bool)
    df["amount_paid"] = df.amount_paid.fillna(0.0)
    df["expected"] = df.fee if EXPECTED_BASIS == "selected" else df.fee.where(df.attended, 0.0)
    df["outstanding"] = (df.expected - df.amount_paid).clip(lower=0).where(~df.paid, 0.0)
    return df


# ---------------- pages ----------------
def page_teams():
    st.header("Teams & Players")
    with st.form("new_team", clear_on_submit=True):
        n = st.text_input("New team / group name")
        if st.form_submit_button("Add team") and n.strip():
            try:
                run(teams.insert().values(name=n.strip()))
                st.rerun()
            except sa.exc.IntegrityError:
                st.error("That team already exists.")
    tid = pick_team("tp_team")
    cur = q("select name from teams where id=:i", i=tid).name.iloc[0]
    c1, c2 = st.columns([3, 1])
    new_name = c1.text_input("Rename team", cur)
    if c2.button("Rename") and new_name.strip() != cur:
        try:
            run(teams.update().where(teams.c.id == tid).values(name=new_name.strip()))
            st.rerun()
        except sa.exc.IntegrityError:
            st.error("That name is taken.")

    st.subheader("Players")
    st.caption("Add rows at the bottom. Untick Active (or delete a row) to retire a player - history is kept.")
    df = q("select id, name, contact, active from players where team_id=:t order by name", t=tid)
    df["active"] = df.active.astype(bool)
    key = f"pl{tid}"
    ed = st.data_editor(df, num_rows="dynamic", hide_index=True, use_container_width=True, key=key,
                        column_config={"id": None, "name": "Name", "contact": "Contact (optional)",
                                       "active": st.column_config.CheckboxColumn("Active", default=True)})
    if st.button("Save players", type="primary"):
        keep = set()
        try:
            with eng.begin() as c:
                for _, r in ed.iterrows():
                    nm = clean(r["name"])
                    if not nm:
                        continue
                    act = True if pd.isna(r["active"]) else bool(r["active"])
                    if pd.notna(r["id"]):
                        keep.add(int(r["id"]))
                        c.execute(players.update().where(players.c.id == int(r["id"]))
                                  .values(name=nm, contact=clean(r["contact"]), active=act))
                    else:
                        c.execute(players.insert().values(team_id=tid, name=nm,
                                                          contact=clean(r["contact"]), active=act))
                for i in set(int(x) for x in df.id) - keep:  # removed rows -> retire, never delete
                    c.execute(players.update().where(players.c.id == i).values(active=False))
        except sa.exc.IntegrityError:
            st.error("Duplicate player name in this team.")
        else:
            st.session_state.pop(key, None)
            st.rerun()


def session_form(tid, r=None):
    sid = int(r["id"]) if r is not None else -1
    opts = q("""select id, name from players where team_id=:t and
                (active = :a or id in (select player_id from session_players where session_id=:s))
                order by name""", t=tid, a=True, s=sid)
    names = dict(zip(opts.id.tolist(), opts.name))
    default = (q("select player_id from session_players where session_id=:s", s=sid).player_id.tolist()
               if r is not None else list(names))
    with st.form(f"sess{sid}"):
        d = st.date_input("Practice date", to_date(r["session_date"]) if r is not None else date.today())
        venue = st.text_input("Venue (optional)", clean(r["venue"]) if r is not None else "")
        fee = st.number_input("Fee per player (£)", min_value=0.0, step=0.5,
                              value=float(r["fee"] or 0) if r is not None else 0.0)
        notes = st.text_area("Notes (optional)", clean(r["notes"]) if r is not None else "")
        ids = st.multiselect("Players selected (voted yes)", list(names), default=[i for i in default if i in names],
                             format_func=lambda i: names[i])
        if st.form_submit_button("Save session", type="primary"):
            if not ids:
                st.error("Select at least one player.")
                return
            try:
                with eng.begin() as c:
                    if r is None:
                        s_id = c.execute(sessions.insert().values(
                            team_id=tid, session_date=d, venue=venue.strip(), fee=fee, notes=notes)
                        ).inserted_primary_key[0]
                    else:
                        s_id = sid
                        c.execute(sessions.update().where(sessions.c.id == sid).values(
                            session_date=d, venue=venue.strip(), fee=fee, notes=notes))
                    have = {x[0] for x in c.execute(sa.select(sp.c.player_id).where(sp.c.session_id == s_id))}
                    for p in set(ids) - have:
                        c.execute(sp.insert().values(session_id=s_id, player_id=int(p)))
                    for p in have - set(ids):
                        c.execute(sp.delete().where(sp.c.session_id == s_id, sp.c.player_id == p))
            except sa.exc.IntegrityError:
                st.error("A session for this team, date and venue already exists.")
            else:
                st.rerun()


def page_sessions():
    st.header("Nets Sessions")
    tid = pick_team("ses_team")
    h = q("""select s.session_date, s.venue, s.fee, s.notes, count(sp.id) as selected,
             coalesce(sum(case when sp.attended then 1 else 0 end), 0) as attended
             from sessions s left join session_players sp on sp.session_id = s.id
             where s.team_id = :t group by s.id, s.session_date, s.venue, s.fee, s.notes
             order by s.session_date desc""", t=tid)
    h["session_date"] = pd.to_datetime(h.session_date).dt.date
    h.insert(0, "status", ["Upcoming" if d > date.today() else "Done" for d in h.session_date])
    with st.expander("Session history", expanded=True):
        st.dataframe(h, hide_index=True, use_container_width=True)
    t_new, t_edit = st.tabs(["➕ New session", "✏️ Edit session"])
    with t_new:
        session_form(tid)
    with t_edit:
        r = pick_session(tid, "ses_edit")
        if r is None:
            st.info("No sessions yet.")
        else:
            st.caption("Removing a player here also removes their attendance/payment row for this session.")
            session_form(tid, r)


def page_attendance():
    st.header("Attendance & Payments")
    tid = pick_team("att_team")
    r = pick_session(tid, "att_sess")
    if r is None:
        st.info("No sessions yet.")
        return
    sid, fee = int(r["id"]), float(r["fee"] or 0)
    d = q("""select sp.id, p.name, sp.attended, sp.paid, sp.amount_paid, sp.paid_date
             from session_players sp join players p on p.id = sp.player_id
             where sp.session_id = :s order by p.name""", s=sid)
    if d.empty:
        st.warning("No players selected for this session.")
        return
    for c in ("attended", "paid"):
        d[c] = d[c].fillna(False).astype(bool)
    d["amount_paid"] = d.amount_paid.fillna(0.0)
    d["paid_date"] = [to_date(v) for v in d.paid_date]
    st.caption(f"Fee per player: £{fee:,.2f}. Tick Paid and Save to record the full fee. "
               "Type an amount only for a part-payment.")
    if st.button("✅ Mark all attended players as paid (full fee)"):
        with eng.begin() as c:
            c.execute(sp.update().where(sp.c.session_id == sid, sp.c.attended.is_(True),
                                        sp.c.paid.is_(False))
                      .values(paid=True, amount_paid=fee, paid_date=date.today()))
        st.session_state.pop(f"att{sid}", None)
        st.rerun()
    key = f"att{sid}"
    ed = st.data_editor(
        d, hide_index=True, use_container_width=True, key=key, disabled=["name"],
        column_config={"id": None, "name": "Player",
                       "attended": st.column_config.CheckboxColumn("Attended"),
                       "paid": st.column_config.CheckboxColumn("Paid"),
                       "amount_paid": st.column_config.NumberColumn("Amount paid (£)", min_value=0.0,
                                                                    step=0.5, format="%.2f"),
                       "paid_date": st.column_config.DateColumn("Paid on")})
    if st.button("Save attendance & payments", type="primary"):
        orig = d.set_index("id")
        with eng.begin() as c:
            for _, x in ed.iterrows():
                rid = int(x["id"])
                amt = 0.0 if pd.isna(x["amount_paid"]) else float(x["amount_paid"])
                paid = bool(x["paid"])
                if paid and amt == 0:
                    amt = fee    # ticked Paid, nothing typed: use the session fee
                elif not paid and orig.loc[rid, "paid"] and amt == orig.loc[rid, "amount_paid"]:
                    amt = 0.0    # un-ticked Paid: clear the auto-filled amount
                pdte = to_date(x["paid_date"]) or (date.today() if (paid or amt > 0) else None)
                c.execute(sp.update().where(sp.c.id == rid).values(
                    attended=bool(x["attended"]), paid=paid, amount_paid=amt, paid_date=pdte))
        st.session_state.pop(key, None)
        st.rerun()

    s = all_rows()
    s = s[s.session_id == sid]
    n, att = len(s), int(s.attended.sum())
    paid_n, pend = int(s.paid.sum()), int((s.outstanding > 0).sum())
    st.subheader("Session summary")
    m = st.columns(4)
    m[0].metric("Selected", n)
    m[1].metric("Attended", att)
    m[2].metric("Did not attend", n - att)
    m[3].metric("Paid", paid_n)
    m = st.columns(4)
    m[0].metric("Pending payment", pend)
    m[1].metric("Expected", f"£{s.expected.sum():,.2f}")
    m[2].metric("Collected", f"£{s.amount_paid.sum():,.2f}")
    m[3].metric("Outstanding", f"£{s.outstanding.sum():,.2f}")
    a, b = st.columns(2)
    a.bar_chart(pd.DataFrame({"Players": [att, n - att]}, index=["Attended", "Did not attend"]), color=GREEN)
    b.bar_chart(pd.DataFrame({"Players": [paid_n, pend]}, index=["Paid", "Pending"]), color=GREEN)


def dash_data(key):
    """Shared filters + per-player stats. Returns (df, g, ses) or None.
    Attendance is measured against ALL sessions the team held (up to today), not just the ones a player polled for."""
    df = all_rows()
    if df.empty:
        st.info("No data yet.")
        return None
    today = date.today()
    c1, c2, c3 = st.columns(3)
    rng = c1.date_input("Date range", (df.session_date.min().date(), df.session_date.max().date()), key=f"{key}_rng")
    tm = c2.multiselect("Team", sorted(df.team.unique()), key=f"{key}_tm")
    pl = c3.multiselect("Player", sorted(df.player.unique()), key=f"{key}_pl")
    ses = q("select s.id as session_id, t.name as team, s.session_date, s.venue "
            "from sessions s join teams t on t.id = s.team_id")
    ses["session_date"] = pd.to_datetime(ses.session_date)

    def keep(x):
        x = x[x.session_date.dt.date <= today]  # upcoming sessions are excluded
        if len(rng) == 2:
            x = x[x.session_date.dt.date.between(rng[0], rng[1])]
        return x[x.team.isin(tm)] if tm else x

    df, ses = keep(df), keep(ses)
    if pl:
        df = df[df.player.isin(pl)]
    if ses.empty:
        st.warning("No sessions match the filters.")
        return None

    agg = df.groupby("player_id").agg(
        polled_yes=("id", "count"), attended=("attended", "sum"), expected=("expected", "sum"),
        paid=("amount_paid", "sum"), outstanding=("outstanding", "sum")).reset_index()
    base = q("select p.id as player_id, p.name as player, t.name as team, p.active "
             "from players p join teams t on t.id = p.team_id")
    if tm:
        base = base[base.team.isin(tm)]
    if pl:
        base = base[base.player.isin(pl)]
    base = base[base.active.astype(bool) | base.player_id.isin(agg.player_id)]
    g = base.drop(columns="active").merge(agg, on="player_id", how="left")
    num = ["polled_yes", "attended", "expected", "paid", "outstanding"]
    g[num] = g[num].fillna(0)
    g[["polled_yes", "attended"]] = g[["polled_yes", "attended"]].astype(int)
    if g.empty:
        st.warning("No players match the filters.")
        return None
    g["total_sessions"] = g.team.map(ses.groupby("team").size()).fillna(0).astype(int)
    g["total_sessions"] = g[["total_sessions", "polled_yes"]].max(axis=1)
    g["no_show"] = g.polled_yes - g.attended
    g["attendance_%"] = (100 * g.attended / g.total_sessions.where(g.total_sessions > 0)).fillna(0).round(1)
    g["showup_%"] = (100 * g.attended / g.polled_yes.where(g.polled_yes > 0)).fillna(0).round(1)
    g["payment_%"] = (100 * (1 - g.outstanding / g.expected.where(g.expected > 0))).fillna(100).clip(0, 100).round(1)
    g["label"] = g.player if g.team.nunique() == 1 else g.player + " (" + g.team + ")"
    return df, g, ses


def page_dashboard():
    st.header("Overall Dashboard")
    r = dash_data("dash")
    if r is None:
        return
    df, g, ses = r
    m = st.columns(4)
    m[0].metric("Sessions held", len(ses))
    m[1].metric("Total attendances", int(df.attended.sum()))
    m[2].metric("Collected", f"£{df.amount_paid.sum():,.2f}")
    m[3].metric("Outstanding", f"£{df.outstanding.sum():,.2f}")
    st.caption("Attendance % = sessions attended ÷ all sessions the team held, whether or not the player "
               "voted yes. Upcoming sessions are excluded.")

    st.subheader("Attendance by player (out of total sessions)")
    mx = int(g.total_sessions.max())
    order = g.sort_values(["attendance_%", "attended"], ascending=False).label.tolist()
    g["text"] = (g.attended.astype(str) + "/" + g.total_sessions.astype(str)
                 + "  (" + g["attendance_%"].astype(str) + "%)")
    sc = alt.Scale(domain=[0, mx * 1.35])
    y = alt.Y("label:N", sort=order, title=None)
    ticks = list(range(0, mx + 1, max(1, mx // 10)))
    track = alt.Chart(g).mark_bar(color="#D9D3C0").encode(
        x=alt.X("total_sessions:Q", scale=sc, title="Sessions (full bar = all sessions held)",
                axis=alt.Axis(values=ticks)), y=y)
    done = alt.Chart(g).mark_bar(color=GREEN).encode(
        x=alt.X("attended:Q", scale=sc, title=None, axis=None), y=y,
        tooltip=[alt.Tooltip("label:N", title="Player"), alt.Tooltip("attended:Q", title="Attended"),
                 alt.Tooltip("total_sessions:Q", title="Total sessions"),
                 alt.Tooltip("attendance_%:Q", title="Attendance %")])
    txt = alt.Chart(g).mark_text(align="left", dx=5, color="#1F2A24").encode(
        x=alt.X("total_sessions:Q", scale=sc, title=None, axis=None), y=y, text="text:N")
    st.altair_chart((track + done + txt).properties(height=max(220, 34 * len(g))), use_container_width=True)

    st.subheader("Player-wise statistics")
    cols = ["team", "player", "total_sessions", "polled_yes", "attended", "no_show", "attendance_%",
            "showup_%", "expected", "paid", "outstanding", "payment_%"]
    st.dataframe(g[cols].sort_values("attendance_%", ascending=False), hide_index=True, use_container_width=True)

    thr = st.slider("Flag players with attendance below (%)", 0, 100, 60)
    flag = g[(g["attendance_%"] < thr) | (g.outstanding > 0)]
    st.subheader("Needs attention (low attendance or pending payments)")
    st.dataframe(flag[["team", "player", "attendance_%", "no_show", "outstanding"]],
                 hide_index=True, use_container_width=True)

    t = df.groupby("session_date").agg(attended=("attended", "sum"), collected=("amount_paid", "sum"))
    a, b = st.columns(2)
    a.caption("Attendees per session")
    a.line_chart(t.attended, color=GREEN)
    b.caption("Collected per session (£)")
    b.bar_chart(t.collected, color=GREEN)

    out = df.drop(columns=["id", "session_id", "team_id", "player_id"])
    out["session_date"] = out.session_date.dt.date
    d1, d2 = st.columns(2)
    d1.download_button("⬇️ CSV", out.to_csv(index=False).encode(), "nets_report.csv", "text/csv")
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        out.to_excel(w, index=False, sheet_name="Detail")
        g[cols].to_excel(w, index=False, sheet_name="Players")
    d2.download_button("⬇️ Excel", buf.getvalue(), "nets_report.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def page_poll():
    st.header("Poll vs Attendance")
    r = dash_data("poll")
    if r is None:
        return
    df, g, ses = r
    polled, att = len(df), int(df.attended.sum())
    m = st.columns(4)
    m[0].metric("Polled yes", polled)
    m[1].metric("Attended", att)
    m[2].metric("Did not attend", polled - att)
    m[3].metric("Show-up rate", f"{(100 * att / polled if polled else 0):.1f}%")
    st.caption("Counts only players who voted yes (were selected). Upcoming sessions are excluded.")
    domain = alt.Scale(domain=["Attended", "Did not attend"], range=[GREEN, "#B3261E"])
    legend = alt.Legend(orient="bottom", title=None)

    st.subheader("By player: polled yes, attended vs did not attend")
    gp = g[g.polled_yes > 0]
    if gp.empty:
        st.info("No polled players in this selection.")
    else:
        order = gp.sort_values(["polled_yes", "attended"], ascending=False).label.tolist()
        lp = gp.melt(id_vars=["label"], value_vars=["attended", "no_show"], var_name="status", value_name="sessions")
        lp["status"] = lp.status.map({"attended": "Attended", "no_show": "Did not attend"})
        st.altair_chart(alt.Chart(lp).mark_bar().encode(
            x=alt.X("sessions:Q", title="Sessions polled yes", axis=alt.Axis(tickMinStep=1)),
            y=alt.Y("label:N", sort=order, title=None),
            color=alt.Color("status:N", scale=domain, legend=legend),
            tooltip=[alt.Tooltip("label:N", title="Player"), alt.Tooltip("status:N", title="Status"),
                     alt.Tooltip("sessions:Q", title="Sessions")],
        ).properties(height=max(220, 34 * len(gp))), use_container_width=True)
        st.dataframe(gp[["team", "player", "polled_yes", "attended", "no_show", "showup_%"]]
                     .sort_values("no_show", ascending=False), hide_index=True, use_container_width=True)

    st.subheader("By session")
    ps = (df.groupby(["session_id", "session_date", "venue"])
          .agg(polled=("id", "count"), attended=("attended", "sum")).reset_index().sort_values("session_date"))
    ps["no_show"] = ps.polled - ps.attended
    ps["label"] = ps.session_date.dt.strftime("%d %b %Y") + ps.venue.apply(lambda v: f" {v}" if v else "")
    ls = ps.melt(id_vars=["label"], value_vars=["attended", "no_show"], var_name="status", value_name="players")
    ls["status"] = ls.status.map({"attended": "Attended", "no_show": "Did not attend"})
    st.altair_chart(alt.Chart(ls).mark_bar().encode(
        x=alt.X("label:N", sort=ps.label.tolist(), title=None, axis=alt.Axis(labelAngle=-45)),
        y=alt.Y("players:Q", title="Players polled yes", axis=alt.Axis(tickMinStep=1)),
        color=alt.Color("status:N", scale=domain, legend=legend),
        tooltip=[alt.Tooltip("label:N", title="Session"), alt.Tooltip("status:N", title="Status"),
                 alt.Tooltip("players:Q", title="Players")],
    ).properties(height=320), use_container_width=True)


def page_profile():
    st.header("Player Profile")
    tid = pick_team("pr_team")
    pl = q("select id, name from players where team_id=:t order by name", t=tid)
    if pl.empty:
        st.info("No players in this team.")
        return
    name = st.selectbox("Player", pl.name)
    pid = int(pl.loc[pl.name == name, "id"].iloc[0])
    h = all_rows()
    h = h[h.player_id == pid].sort_values("session_date", ascending=False)
    if h.empty:
        st.info("This player has not been selected for any session yet.")
        return
    att = int(h.attended.sum())
    m = st.columns(5)
    m[0].metric("Selected", len(h))
    m[1].metric("Attended", att)
    m[2].metric("Missed", len(h) - att)
    m[3].metric("Paid", f"£{h.amount_paid.sum():,.2f}")
    m[4].metric("Outstanding", f"£{h.outstanding.sum():,.2f}")
    v = h[["session_date", "venue", "fee", "attended", "paid", "amount_paid", "paid_date", "outstanding"]].copy()
    v["session_date"] = v.session_date.dt.date
    v["paid_date"] = [to_date(x) for x in v.paid_date]
    st.dataframe(v, hide_index=True, use_container_width=True)


nav = st.navigation({
    "Manage": [
        st.Page(page_teams, title="Teams & Players", icon=":material/groups:"),
        st.Page(page_sessions, title="Sessions", icon=":material/event:"),
        st.Page(page_attendance, title="Attendance & Payments", icon=":material/fact_check:"),
    ],
    "Insights": [
        st.Page(page_dashboard, title="Dashboard", icon=":material/monitoring:", default=True),
        st.Page(page_poll, title="Poll vs Attendance", icon=":material/compare_arrows:"),
        st.Page(page_profile, title="Player Profile", icon=":material/person:"),
    ],
})
st.sidebar.divider()
st.sidebar.caption(f"{CLUB_NAME}  |  DB: {eng.dialect.name} @ {eng.url.host or 'local file'}")
nav.run()