import secrets
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from app.analysis import build_analysis
from app.compat import model_to_dict
from app.config import get_settings
from app.holdings import build_holdings_snapshot, load_holdings, save_holdings
from app.logging_setup import configure_logging
from app.market_data import MarketDataError, build_market_data_provider
from app.performance import evaluate_recommendation_performance
from app.production_status import build_production_status
from app.recommendations import RUN_SLOT_AUTO, RUN_SLOT_CONTEXTS, RecommendationService
from app.schemas import AnalyzeRequest, AnalyzeResponse, HoldingsUpdate, WatchlistUpdate
from app.storage import read_json
from app.watchlist import load_watchlist, save_watchlist


DISCLAIMER = "仅供个人量化研究和交易辅助，不构成投资建议；实盘前请结合仓位、流动性、交易成本和个人风险承受能力。"
SETTINGS = get_settings()
DATA_PROVIDER = build_market_data_provider(
    SETTINGS.market_data_provider,
    cache_ttl_seconds=SETTINGS.cache_ttl_seconds,
    disk_cache_path=SETTINGS.market_data_cache_path,
    tushare_fallback_to_akshare=SETTINGS.tushare_fallback_to_akshare,
    tushare_token=SETTINGS.tushare_token,
    enable_mootdx_daily_fallback=SETTINGS.enable_mootdx_daily_fallback,
    mootdx_servers=SETTINGS.mootdx_servers,
    mootdx_timeout_seconds=SETTINGS.mootdx_timeout_seconds,
    mootdx_daily_max_pages=SETTINGS.mootdx_daily_max_pages,
    mootdx_daily_max_elapsed_seconds=SETTINGS.mootdx_daily_max_elapsed_seconds,
)
RECOMMENDATIONS = RecommendationService(SETTINGS, DATA_PROVIDER, DISCLAIMER)
SECURITY = HTTPBasic(auto_error=False)


def require_basic_auth(credentials: Optional[HTTPBasicCredentials] = Depends(SECURITY)) -> None:
    if not SETTINGS.basic_auth_user and not SETTINGS.basic_auth_password:
        return
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )

    username_ok = secrets.compare_digest(credentials.username, SETTINGS.basic_auth_user)
    password_ok = secrets.compare_digest(credentials.password, SETTINGS.basic_auth_password)
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


def _build_analysis(request: AnalyzeRequest) -> AnalyzeResponse:
    return build_analysis(request, DATA_PROVIDER, DISCLAIMER)


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title=SETTINGS.api_title, version=SETTINGS.api_version)

    if SETTINGS.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=SETTINGS.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT"],
            allow_headers=["Authorization", "Content-Type"],
        )

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.exception_handler(MarketDataError)
    async def market_data_error_handler(request, exc: MarketDataError):
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"error": {"code": "MARKET_DATA_ERROR", "message": str(exc)}},
        )

    @app.get("/health")
    def health():
        return {
            "ok": True,
            "service": SETTINGS.api_title,
            "version": SETTINGS.api_version,
            "auth": "enabled" if SETTINGS.basic_auth_user else "disabled",
        }

    @app.get("/api/markets", dependencies=[Depends(require_basic_auth)])
    def markets():
        return {
            "markets": [
                {"id": "a", "name": "A 股", "example": "600519"},
                {"id": "etf", "name": "A 股 ETF", "example": "510300"},
            ],
            "adjust_options": [
                {"id": "qfq", "name": "前复权"},
                {"id": "", "name": "不复权"},
                {"id": "hfq", "name": "后复权"},
            ],
        }

    @app.post("/api/analyze", response_model=AnalyzeResponse, dependencies=[Depends(require_basic_auth)])
    def analyze(request: AnalyzeRequest):
        return _build_analysis(request)

    @app.get("/api/watchlist", dependencies=[Depends(require_basic_auth)])
    def get_watchlist():
        return {"items": load_watchlist(SETTINGS.watchlist_path)}

    @app.put("/api/watchlist", dependencies=[Depends(require_basic_auth)])
    def update_watchlist(payload: WatchlistUpdate):
        items = [model_to_dict(item) for item in payload.items]
        save_watchlist(SETTINGS.watchlist_path, items)
        return {"items": items}

    @app.post("/api/watchlist/analyze", dependencies=[Depends(require_basic_auth)])
    def analyze_watchlist():
        items = load_watchlist(SETTINGS.watchlist_path)
        results = []
        errors = []
        for item in items:
            try:
                request = AnalyzeRequest(
                    symbol=item["symbol"],
                    market=item["market"],
                    name=item.get("name"),
                    lookback_days=360,
                    adjust="qfq",
                )
                results.append(model_to_dict(_build_analysis(request)))
            except Exception as exc:
                errors.append(
                    {
                        "symbol": item.get("symbol"),
                        "market": item.get("market"),
                        "name": item.get("name"),
                        "message": str(exc),
                    }
                )
        return {"items": results, "errors": errors, "disclaimer": DISCLAIMER}

    @app.get("/api/holdings", dependencies=[Depends(require_basic_auth)])
    def get_holdings():
        return build_holdings_snapshot(
            SETTINGS.holdings_path,
            DATA_PROVIDER,
            DISCLAIMER,
            RECOMMENDATIONS.l1_quotes,
        )

    @app.put("/api/holdings", dependencies=[Depends(require_basic_auth)])
    def update_holdings(payload: HoldingsUpdate):
        items = [model_to_dict(item) for item in payload.items]
        save_holdings(SETTINGS.holdings_path, items)
        return {"items": load_holdings(SETTINGS.holdings_path)}

    @app.get("/api/recommendations/latest", dependencies=[Depends(require_basic_auth)])
    def latest_recommendations():
        return RECOMMENDATIONS.latest()

    @app.get("/api/performance/recommendations", dependencies=[Depends(require_basic_auth)])
    def recommendation_performance(limit: int = Query(500, ge=10, le=2000)):
        return evaluate_recommendation_performance(
            SETTINGS.recommendation_history_path,
            DATA_PROVIDER,
            SETTINGS,
            limit=limit,
        )

    @app.get("/api/data/akshare-status", dependencies=[Depends(require_basic_auth)])
    def akshare_status():
        return read_json(
            SETTINGS.akshare_status_path,
            {"updated_at": None, "endpoints": {}, "events": []},
        )

    @app.get("/api/production/status", dependencies=[Depends(require_basic_auth)])
    def production_status():
        return build_production_status(SETTINGS)

    @app.post("/api/recommendations/run", dependencies=[Depends(require_basic_auth)])
    def run_recommendations(
        background_tasks: BackgroundTasks,
        force: bool = Query(False),
        max_deep: Optional[int] = Query(None, ge=20, le=2000),
        run_slot: str = Query(RUN_SLOT_AUTO, pattern="^(auto|pre_open|open_confirm|pre_close|post_close)$"),
        background: bool = Query(True),
    ):
        if run_slot not in {RUN_SLOT_AUTO, *RUN_SLOT_CONTEXTS.keys()}:
            raise HTTPException(status_code=400, detail="Invalid run_slot")
        if background:
            payload, lock_token = RECOMMENDATIONS.begin_recommendation_run(
                max_deep=max_deep,
                run_slot=run_slot,
            )
            if lock_token:
                background_tasks.add_task(
                    RECOMMENDATIONS.generate_daily_recommendations,
                    force,
                    max_deep,
                    None,
                    lock_token,
                    run_slot,
                )
            return payload
        return RECOMMENDATIONS.generate_daily_recommendations(
            force=force,
            max_deep=max_deep,
            run_slot=run_slot,
        )

    @app.get("/api/alerts/recent", dependencies=[Depends(require_basic_auth)])
    def recent_alerts(limit: int = Query(100, ge=1, le=500)):
        return {"items": RECOMMENDATIONS.recent_alerts(limit=limit)}

    @app.post("/api/alerts/monitor", dependencies=[Depends(require_basic_auth)])
    def monitor_alerts(force: bool = Query(False)):
        return RECOMMENDATIONS.monitor_recommendations(force=force)

    @app.post("/api/alerts/planned-exits", dependencies=[Depends(require_basic_auth)])
    def planned_exits_alerts(force: bool = Query(False)):
        return RECOMMENDATIONS.monitor_planned_exits(force=force)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(static_dir / "index.html")

    @app.get("/{path:path}", include_in_schema=False)
    def spa_fallback(path: str):
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(static_dir / "index.html")

    return app


app = create_app()
