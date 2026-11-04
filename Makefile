NAME = null-city

# Colors
GREEN  = \033[0;32m
YELLOW = \033[0;33m
BLUE   = \033[0;34m
RESET  = \033[0m

.PHONY: all up down run test clean fclean re help

all: up

# Docker Compose background launch (evaluation target)
up:
	@test -f .env || (echo "$(YELLOW)Creating .env from .env.example...$(RESET)" && cp .env.example .env)
	@echo "$(GREEN)Starting $(NAME) with Docker Compose...$(RESET)"
	docker compose up --build -d

# Stop Docker Compose containers
down:
	@echo "$(YELLOW)Stopping $(NAME) containers...$(RESET)"
	docker compose down

# Native HTTPS launch (development / fallback mode)
run:
	@test -f .env || (echo "$(YELLOW)Creating .env from .env.example...$(RESET)" && cp .env.example .env)
	@echo "$(BLUE)Starting $(NAME) in native HTTPS mode...$(RESET)"
	python3 scripts/serve.py

# Run all verification tests (syntax, frontend, backend)
test:
	@echo "$(BLUE)Running syntax check...$(RESET)"
	npm run check
	@echo "$(BLUE)Running frontend tests...$(RESET)"
	npm test
	@echo "$(BLUE)Running backend tests...$(RESET)"
	npm run test:server
	@echo "$(GREEN)All tests passed successfully!$(RESET)"

# Remove temporary files and stop containers
clean:
	@echo "$(YELLOW)Cleaning temporary files...$(RESET)"
	@docker compose down --remove-orphans 2>/dev/null || true
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -name "*.pyc" -delete 2>/dev/null || true
	@rm -rf tmp/ .pytest_cache/
	@echo "$(GREEN)Clean complete.$(RESET)"

# Deep clean: remove containers, volumes, images, certs and database
fclean: clean
	@echo "$(YELLOW)Full clean: removing containers, volumes, certs and data...$(RESET)"
	@docker compose down -v --rmi local --remove-orphans 2>/dev/null || true
	@rm -rf data/ *.sqlite3* *.pem *.key NULL-CITY.html
	@echo "$(GREEN)Full clean complete.$(RESET)"

# Rebuild and restart
re: fclean up

help:
	@echo "$(BLUE)Available targets:$(RESET)"
	@echo "  $(GREEN)make up$(RESET)     - Build and run containers in background (Docker Compose)"
	@echo "  $(GREEN)make down$(RESET)   - Stop containers (Docker Compose)"
	@echo "  $(GREEN)make run$(RESET)    - Run native HTTPS server (Python 3.12+)"
	@echo "  $(GREEN)make test$(RESET)   - Run full test suite (syntax + frontend + backend)"
	@echo "  $(GREEN)make clean$(RESET)  - Stop containers and remove temporary cache files"
	@echo "  $(GREEN)make fclean$(RESET) - Full clean (containers, volumes, data, certs)"
	@echo "  $(GREEN)make re$(RESET)     - fclean and up"
