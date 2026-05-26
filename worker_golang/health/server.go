// Package health provides a lightweight HTTP health endpoint on /health.
// Exposed on PORT (default 8006) for Docker healthchecks.
package health

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

// Serve starts the HTTP health server in the foreground.
// Call in a goroutine from main.
func Serve(pool *pgxpool.Pool) {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8006"
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/health", makeHandler(pool))

	addr := fmt.Sprintf(":%s", port)
	log.Printf("[health] listening on %s", addr)
	if err := http.ListenAndServe(addr, mux); err != nil {
		log.Printf("[health] server error: %v", err)
	}
}

func makeHandler(pool *pgxpool.Pool) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		checks := map[string]string{}
		overallOK := true

		// Postgres ping
		ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()

		if err := pool.Ping(ctx); err != nil {
			checks["database"] = fmt.Sprintf("error: %v", err)
			overallOK = false
		} else {
			checks["database"] = "ok"
		}

		status := "healthy"
		httpStatus := http.StatusOK
		if !overallOK {
			status = "degraded"
			httpStatus = http.StatusServiceUnavailable
		}

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(httpStatus)
		_ = json.NewEncoder(w).Encode(map[string]interface{}{
			"service": "reportes_worker_golang",
			"status":  status,
			"checks":  checks,
		})
	}
}
