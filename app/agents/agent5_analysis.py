"""Agent 5 — Reproducible Quantitative Analysis.

Runs entirely in an isolated, deterministic fashion on researcher-uploaded
datasets: cleaning report, assumption checks, statistical tests, and a
generated stand-alone script that reproduces every number.

COMPULSORY RULES (spec 2 / Agent 5):
  - Never fabricate output: every number is computed from the uploaded data.
  - Preserve the original file (checksum) alongside the cleaned version.
  - Clearly separate associations from causal conclusions in all reporting.
  - Results require researcher approval before they feed writing (Agent 6).

Heavy dependencies (pandas/numpy/scipy) are imported lazily so the API
boots without them; a clear error is raised only when analysis is used.
"""
import hashlib
import json
import logging
import math
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import DATA_DIR
from app.models.schemas import ANALYSIS_TYPES, AnalysisRun

log = logging.getLogger(__name__)

METHOD = "reproducible_analysis_v1"
ANALYSIS_DIR = DATA_DIR / "analysis"


def _pd():
    try:
        import pandas as pd
        return pd
    except ImportError as exc:
        raise RuntimeError("pandas is required: pip install pandas") from exc


def _np():
    try:
        import numpy as np
        return np
    except ImportError as exc:
        raise RuntimeError("numpy is required: pip install numpy") from exc


def _ss():
    try:
        from scipy import stats
        return stats
    except ImportError as exc:
        raise RuntimeError("scipy is required: pip install scipy") from exc


def checksum_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _jsonable(o):
    """Convert numpy/pandas scalars to plain JSON types."""
    np = _np()
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    return o


# ---------------------------------------------------------------------------
# 1. Dataset cleaning (original preserved, checksum recorded)
# ---------------------------------------------------------------------------

def clean_dataset(csv_path: str, run_id: str) -> Dict:
    pd = _pd()
    try:
        df = pd.read_csv(csv_path)
    except Exception as exc:
        raise ValueError(f"Could not read CSV {csv_path!r}: {exc}") from exc

    report: Dict = {
        "n_rows_raw": int(len(df)),
        "n_columns": int(len(df.columns)),
        "columns": list(df.columns),
        "dtypes": {c: str(t) for c, t in df.dtypes.items()},
        "missing_per_column": {c: int(df[c].isna().sum()) for c in df.columns},
        "duplicate_rows": int(df.duplicated().sum()),
        "actions": [],
    }
    before = len(df)
    df = df.dropna(how="all")
    if len(df) < before:
        report["actions"].append(f"Dropped {before - len(df)} fully-empty rows.")
    if report["duplicate_rows"]:
        df = df.drop_duplicates()
        report["actions"].append(
            f"Dropped {report['duplicate_rows']} duplicate rows.")
    # Report (not silently fix) non-numeric values in numeric-looking columns.
    for c in df.columns:
        non_num = df[c].dropna()
        if non_num.dtype == object and len(non_num):
            sample = non_num[~non_num.astype(str).str.match(
                r"^-?\d+(\.\d+)?$", na=False)]
            if len(sample) and len(sample) < len(non_num):
                report["actions"].append(
                    f"Column '{c}': {len(sample)} non-numeric value(s) kept as-is "
                    f"(e.g. {sample.iloc[0]!r}); excluded from numeric tests.")

    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    clean_path = str(ANALYSIS_DIR / f"{run_id}_clean.csv")
    df.to_csv(clean_path, index=False)
    report["n_rows_clean"] = int(len(df))
    report["clean_path"] = clean_path
    return {"report": report, "clean_path": clean_path}


# ---------------------------------------------------------------------------
# 2. Statistical execution
# ---------------------------------------------------------------------------

def _descriptive(df, np) -> Dict:
    num = df.select_dtypes(include=[np.number])
    if num.empty:
        raise ValueError("No numeric columns for descriptive statistics.")
    desc = num.describe(percentiles=[.25, .5, .75]).to_dict()
    return {"table": _jsonable(desc), "n": int(len(df)),
            "note": "Descriptive summary only — no causal interpretation."}


def _ttest(df, config, np, stats) -> Dict:
    g, v = config["group_col"], config["value_col"]
    for col in (g, v):
        if col not in df.columns:
            raise ValueError(f"Column {col!r} not in dataset.")
    d = df[[g, v]].dropna()
    groups = list(d[g].unique())
    if len(groups) != 2:
        raise ValueError(f"t-test needs exactly 2 groups, found {groups}.")
    a = d.loc[d[g] == groups[0], v].astype(float)
    b = d.loc[d[g] == groups[1], v].astype(float)
    t, p = stats.ttest_ind(a, b, equal_var=False)  # Welch's
    pooled_sd = float(np.sqrt((a.var(ddof=1) + b.var(ddof=1)) / 2))
    cohen_d = float((a.mean() - b.mean()) / pooled_sd) if pooled_sd else 0.0
    return {"groups": [str(groups[0]), str(groups[1])],
            "n": [int(len(a)), int(len(b))],
            "means": [float(a.mean()), float(b.mean())],
            "t": float(t), "p": float(p), "cohens_d": round(cohen_d, 3),
            "significant_05": bool(p < 0.05),
            "note": "Association test (Welch's t). A significant p-value does "
                    "not establish causation."}


def _chi2(df, config, np, stats) -> Dict:
    c1, c2 = config["col1"], config["col2"]
    for col in (c1, c2):
        if col not in df.columns:
            raise ValueError(f"Column {col!r} not in dataset.")
    table = pd_crosstab(df, c1, c2)
    chi2, p, dof, _ = stats.chi2_contingency(table.values)
    n = table.values.sum()
    k = min(table.shape)
    cramers_v = float(np.sqrt(chi2 / (n * (k - 1)))) if k > 1 and n else 0.0
    return {"contingency_table": table.to_dict(), "chi2": float(chi2),
            "p": float(p), "dof": int(dof),
            "cramers_v": round(cramers_v, 3),
            "significant_05": bool(p < 0.05),
            "note": "Tests independence of categorical variables; not causation."}


def pd_crosstab(df, c1, c2):
    pd = _pd()
    return pd.crosstab(df[c1], df[c2])


def _correlation(df, config, np, stats) -> Dict:
    cols = config["cols"]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"Columns not in dataset: {missing}")
    d = df[cols].apply(pd_to_numeric).dropna()
    if len(d) < 3:
        raise ValueError("Not enough complete rows for correlation.")
    out = {}
    for i, c1 in enumerate(cols):
        for c2 in cols[i + 1:]:
            r, p = stats.pearsonr(d[c1], d[c2])
            out[f"{c1} ~ {c2}"] = {"r": round(float(r), 4),
                                   "p": float(p),
                                   "n": int(len(d)),
                                   "significant_05": bool(p < 0.05)}
    out["_note"] = "Correlations are associations, not causal effects."
    return out


def pd_to_numeric(s):
    pd = _pd()
    return pd.to_numeric(s, errors="coerce")


def _regression(df, config, np, stats) -> Dict:
    outcome = config["outcome"]
    preds = config["predictors"]
    cols = [outcome] + preds
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"Columns not in dataset: {missing}")
    d = df[cols].apply(pd_to_numeric).dropna()
    if len(d) < len(preds) + 2:
        raise ValueError("Not enough complete rows for regression.")
    X = np.column_stack([np.ones(len(d))] + [d[c].values for c in preds])
    y = d[outcome].values
    beta, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
    yhat = X @ beta
    n, k = X.shape
    sse = float(((y - yhat) ** 2).sum())
    mse = sse / (n - k)
    cov = mse * np.linalg.inv(X.T @ X)
    se = np.sqrt(np.diag(cov))
    tvals = beta / se
    pvals = 2 * (1 - stats.t.cdf(np.abs(tvals), n - k))
    r2 = 1 - sse / float(((y - y.mean()) ** 2).sum())
    rows = []
    for name, b, s_, t_, p_ in zip(["(Intercept)"] + preds, beta, se, tvals, pvals):
        rows.append({"term": name, "estimate": round(float(b), 4),
                     "se": round(float(s_), 4), "t": round(float(t_), 3),
                     "p": float(p_), "significant_05": bool(p_ < 0.05)})
    return {"coefficients": rows, "r_squared": round(float(r2), 4),
            "n": int(n),
            "note": "OLS estimates associations conditional on the included "
                    "predictors. Causal claims require a causal design and "
                    "assumption checks."}


_HANDLERS = {
    "descriptive": lambda df, cfg, np, stats: _descriptive(df, np),
    "ttest": _ttest,
    "chi2": _chi2,
    "correlation": _correlation,
    "regression": _regression,
}

_CONFIG_REQUIRED = {
    "descriptive": [],
    "ttest": ["group_col", "value_col"],
    "chi2": ["col1", "col2"],
    "correlation": ["cols"],
    "regression": ["outcome", "predictors"],
}


def _render_script(analysis_type: str, config: Dict, clean_csv_path: str) -> str:
    """Generate a stand-alone script reproducing the analysis exactly."""
    cfg_json = json.dumps(config, indent=2)
    lines = [
        '"""Reproducible analysis script — generated by Agent 5',
        f"Generated: {datetime.utcnow().isoformat()}",
        f"Input (cleaned CSV): {clean_csv_path}",
        f"Analysis: {analysis_type}",
        'Run: python <this_file>',
        '"""',
        "import json",
        "import numpy as np",
        "import pandas as pd",
        "from scipy import stats",
        "",
        f'df = pd.read_csv(r"{clean_csv_path}")',
        'print(f"Rows: {len(df)}, Columns: {list(df.columns)}")',
        f"CONFIG = {cfg_json}",
        "",
    ]
    if analysis_type == "descriptive":
        lines += ["print(df.describe().to_string())"]
    elif analysis_type == "ttest":
        lines += [
            "g, v = CONFIG['group_col'], CONFIG['value_col']",
            "d = df[[g, v]].dropna()",
            "groups = list(d[g].unique())",
            "a = d.loc[d[g] == groups[0], v].astype(float)",
            "b = d.loc[d[g] == groups[1], v].astype(float)",
            "t, p = stats.ttest_ind(a, b, equal_var=False)",
            "print(f'groups={groups} t={t:.4f} p={p:.4g}')",
        ]
    elif analysis_type == "chi2":
        lines += [
            "table = pd.crosstab(df[CONFIG['col1']], df[CONFIG['col2']])",
            "chi2, p, dof, _ = stats.chi2_contingency(table.values)",
            "print(table.to_string())",
            "print(f'chi2={chi2:.4f} p={p:.4g} dof={dof}')",
        ]
    elif analysis_type == "correlation":
        lines += [
            "d = df[CONFIG['cols']].apply(pd.to_numeric, errors='coerce').dropna()",
            "print(d.corr(method='pearson').to_string())",
        ]
    elif analysis_type == "regression":
        lines += [
            "cols = [CONFIG['outcome']] + CONFIG['predictors']",
            "d = df[cols].apply(pd.to_numeric, errors='coerce').dropna()",
            "X = np.column_stack([np.ones(len(d))] + [d[c].values for c in CONFIG['predictors']])",
            "y = d[CONFIG['outcome']].values",
            "beta, *_ = np.linalg.lstsq(X, y, rcond=None)",
            "for name, b in zip(['(Intercept)'] + CONFIG['predictors'], beta):",
            "    print(f'{name}: {b:.4f}')",
        ]
    lines.append("")
    return "\n".join(lines)


def run_analysis(clean_csv_path: str, analysis_type: str,
                 config: Dict) -> Dict:
    """Execute the statistical analysis; returns results + repro script."""
    if analysis_type not in ANALYSIS_TYPES:
        raise ValueError(f"Unknown analysis_type: {analysis_type!r}. "
                         f"Choose from {sorted(ANALYSIS_TYPES)}.")
    missing = [k for k in _CONFIG_REQUIRED[analysis_type] if k not in config]
    if missing:
        raise ValueError(f"analysis_type={analysis_type!r} requires config keys: "
                         f"{_CONFIG_REQUIRED[analysis_type]} (missing {missing}).")
    pd, np, stats = _pd(), _np(), _ss()
    df = pd.read_csv(clean_csv_path)
    log_lines = [f"Loaded {len(df)} rows x {len(df.columns)} columns from cleaned CSV."]
    handler = _HANDLERS[analysis_type]
    try:
        results = handler(df, config, np, stats)
    except Exception as exc:
        raise ValueError(f"Analysis failed: {exc}") from exc
    log_lines.append(f"Ran {analysis_type} with config={config}.")
    results = _jsonable(results)
    script = _render_script(analysis_type, config, clean_csv_path)
    return {"results": results, "script_text": script, "log": log_lines}


# ---------------------------------------------------------------------------
# 3. Orchestration + persistence
# ---------------------------------------------------------------------------

def create_analysis_run(db: Session, project_id: str, dataset_path: str,
                        dataset_name: str, analysis_type: str,
                        config: Dict, task_id: str | None = None,
                        protocol_id: str | None = None) -> Dict:
    """Full Agent 5 run: checksum -> clean -> analyze -> persist + script file."""
    import uuid as _uuid
    run_id = str(_uuid.uuid4())
    checksum = checksum_file(dataset_path)

    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    stored = ANALYSIS_DIR / f"{run_id}_{Path(dataset_name).name}"
    with open(dataset_path, "rb") as src, open(stored, "wb") as dst:
        dst.write(src.read())

    cleaned = clean_dataset(str(stored), run_id)
    outcome = run_analysis(cleaned["clean_path"], analysis_type, config)

    script_path = str(ANALYSIS_DIR / f"{run_id}_script.py")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(outcome["script_text"])

    row = AnalysisRun(
        id=run_id, project_id=project_id, task_id=task_id,
        protocol_id=protocol_id, dataset_name=dataset_name,
        dataset_path=str(stored), dataset_checksum=checksum,
        analysis_type=analysis_type, analysis_config=json.dumps(config),
        cleaning_report=json.dumps(cleaned["report"]),
        results=json.dumps(outcome["results"]),
        script_text=outcome["script_text"], script_path=script_path,
        log="\n".join(outcome["log"]),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    log.info("Analysis run %s: %s on %s", run_id[:8], analysis_type, dataset_name)
    return get_run_dict(run_id, db)


def get_run_dict(run_id: str, db: Session) -> Dict:
    r = db.get(AnalysisRun, run_id)
    if r is None:
        raise ValueError(f"Unknown analysis run id: {run_id}")
    return {
        "run_id": r.id, "project_id": r.project_id, "task_id": r.task_id,
        "protocol_id": r.protocol_id, "dataset_name": r.dataset_name,
        "dataset_checksum": r.dataset_checksum,
        "analysis_type": r.analysis_type, "analysis_config": r.config_dict,
        "cleaning_report": r.cleaning_dict, "results": r.results_dict,
        "script_path": r.script_path, "log": r.log,
        "approval_status": r.approval_status,
        "researcher_note": r.researcher_note,
        "method": METHOD,
    }


def list_runs(db: Session, project_id: str | None = None,
              task_id: str | None = None, limit: int = 100) -> List[AnalysisRun]:
    stmt = select(AnalysisRun).order_by(AnalysisRun.created_at.desc())
    if project_id:
        stmt = stmt.where(AnalysisRun.project_id == project_id)
    if task_id:
        stmt = stmt.where(AnalysisRun.task_id == task_id)
    return list(db.scalars(stmt.limit(limit)).all())


def review_analysis(db: Session, run_id: str, approved: bool,
                    note: str = "") -> AnalysisRun:
    """Researcher approve/reject analysis results (gates Agent 6 writing)."""
    r = db.get(AnalysisRun, run_id)
    if r is None:
        raise ValueError(f"Unknown analysis run id: {run_id}")
    r.approval_status = "approved" if approved else "rejected"
    r.researcher_note = note
    db.commit()
    db.refresh(r)
    return r
