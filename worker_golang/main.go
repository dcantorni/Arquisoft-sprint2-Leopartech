// worker_golang — Golang replacement for the Python Celery reportes_worker.
//
// Consumes from RabbitMQ bite_events topic exchange:
//   bite.eventos   (evento.#)   → handlers.HandleEvento
//   bite.proyectos (proyecto.*) → handlers.HandleProyecto
//   bite.analisis  (analisis.*) → log-and-ack (future expansion)
//   bite.reportes  (reporte.*)  → log-and-ack (future expansion)
//
// Health endpoint: GET /health on PORT (default 8006).
// Graceful shutdown on SIGTERM / SIGINT.
package main

import (
	"context"
	"log"
	"os"
	"os/signal"
	"strconv"
	"sync"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/joho/godotenv"
	amqp "github.com/rabbitmq/amqp091-go"

	"worker_golang/consumer"
	"worker_golang/db"
	"worker_golang/handlers"
	"worker_golang/health"
)

func main() {
	// Load .env if present (local dev only; Docker provides env vars directly)
	_ = godotenv.Load()

	log.Println("[main] starting reportes_worker_golang")

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// ── 1. Connect to PostgreSQL ─────────────────────────────────────────────
	pool, err := db.Connect(ctx)
	if err != nil {
		log.Fatalf("[main] postgres connect: %v", err)
	}
	defer pool.Close()
	log.Println("[main] postgres connected")

	// ── 2. Connect to RabbitMQ ───────────────────────────────────────────────
	rabbitURL := getenv("RABBITMQ_URL", "amqp://bite:bite_pass@rabbitmq:5672/bite_vhost")
	c, err := consumer.New(rabbitURL)
	if err != nil {
		log.Fatalf("[main] rabbitmq connect: %v", err)
	}
	defer c.Close()
	log.Println("[main] rabbitmq connected")

	// ── 3. Start health server ───────────────────────────────────────────────
	go health.Serve(pool)

	// ── 4. Consume queues ────────────────────────────────────────────────────
	concurrency := workerConcurrency()
	log.Printf("[main] worker concurrency = %d", concurrency)

	var wg sync.WaitGroup
	sem := make(chan struct{}, concurrency) // bounding goroutine fan-out

	// bite.eventos — primary batch events from manejador_reportes Django
	eventsMsgs, err := c.Consume("bite.eventos")
	if err != nil {
		log.Fatalf("[main] consume bite.eventos: %v", err)
	}

	// bite.proyectos — proyecto.creado from manejador_usuarios
	proyectosMsgs, err := c.Consume("bite.proyectos")
	if err != nil {
		log.Fatalf("[main] consume bite.proyectos: %v", err)
	}

	// bite.analisis + bite.reportes — reserved for future handlers
	analisMsgs, err := c.Consume("bite.analisis")
	if err != nil {
		log.Fatalf("[main] consume bite.analisis: %v", err)
	}
	reportesMsgs, err := c.Consume("bite.reportes")
	if err != nil {
		log.Fatalf("[main] consume bite.reportes: %v", err)
	}

	connClose := c.NotifyClose()

	// dispatch loop
	go func() {
		for {
			select {
			case <-ctx.Done():
				return

			case err := <-connClose:
				log.Printf("[main] rabbitmq connection closed: %v — shutting down", err)
				cancel()
				return

			case d, ok := <-eventsMsgs:
				if !ok {
					cancel()
					return
				}
				dispatchMsg(&wg, sem, ctx, pool, d, handlers.HandleEvento)

			case d, ok := <-proyectosMsgs:
				if !ok {
					cancel()
					return
				}
				dispatchMsg(&wg, sem, ctx, pool, d, handlers.HandleProyecto)

			case d, ok := <-analisMsgs:
				if !ok {
					cancel()
					return
				}
				logAndAck("analisis", d)

			case d, ok := <-reportesMsgs:
				if !ok {
					cancel()
					return
				}
				logAndAck("reportes", d)
			}
		}
	}()

	// ── 5. Graceful shutdown ─────────────────────────────────────────────────
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGTERM, syscall.SIGINT)

	select {
	case sig := <-sigCh:
		log.Printf("[main] received signal %s — shutting down", sig)
	case <-ctx.Done():
		log.Println("[main] context cancelled — shutting down")
	}

	cancel()

	// Wait for in-flight handlers with a 30 s deadline
	done := make(chan struct{})
	go func() {
		wg.Wait()
		close(done)
	}()

	select {
	case <-done:
		log.Println("[main] all handlers finished — exiting cleanly")
	case <-time.After(30 * time.Second):
		log.Println("[main] shutdown timeout — forcing exit")
	}
}

// dispatchMsg acquires the semaphore and runs handler in a goroutine.
func dispatchMsg(
	wg *sync.WaitGroup,
	sem chan struct{},
	ctx context.Context,
	pool *pgxpool.Pool,
	d amqp.Delivery,
	handler func(context.Context, *pgxpool.Pool, amqp.Delivery),
) {
	sem <- struct{}{}
	wg.Add(1)
	go func() {
		defer func() {
			<-sem
			wg.Done()
		}()
		handler(ctx, pool, d)
	}()
}

func logAndAck(queue string, d amqp.Delivery) {
	log.Printf("[%s] received unhandled message (routing_key=%s) — acking", queue, d.RoutingKey)
	_ = d.Ack(false)
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func workerConcurrency() int {
	if v := os.Getenv("WORKER_CONCURRENCY"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			return n
		}
	}
	return 10
}
