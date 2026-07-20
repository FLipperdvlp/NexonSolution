
# Можно переопределить через переменную окружения GARANT_USERNAME.
GARANT_USERNAME: str = os.getenv("GARANT_USERNAME", "gavrilovit")

# ---------------------------------------------------------------------------
# Логирование
# ---------------------------------------------------------------------------
LOGS_DIR = os.getenv("LOGS_DIR", "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
formatter = logging.Formatter(LOG_FORMAT)

bot_file_handler = TimedRotatingFileHandler(
    filename=os.path.join(LOGS_DIR, "bot.log"),
    when="midnight",
)