// HandleProyecto processes messages from bite.proyectos (routing key proyecto.*).
package handlers

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	amqp "github.com/rabbitmq/amqp091-go"

	"worker_golang/db"
)

// HandleProyecto processes a single delivery from bite.proyectos.
//
// On proyecto.creado events it creates an Analisis + EjecucionAnalisis so the
// Django ReporteListView has data to return.  Unknown subtypes are acked and
// logged (idempotent no-op).
func HandleProyecto(ctx context.Context, pool *pgxpool.Pool, d amqp.Delivery) {
	start := time.Now()

	// ── 1. Parse payload ────────────────────────────────────────────────────
	var event map[string]interface{}
	if err := json.Unmarshal(d.Body, &event); err != nil {
		log.Printf("[proyecto] bad JSON: %v — nacking without requeue", err)
		_ = d.Nack(false, false)
		return
	}

	eventoID := d.MessageId
	if eventoID == "" {
		if id, ok := event["evento_id"].(string); ok && id != "" {
			eventoID = id
		} else {
			eventoID = uuid.New().String()
		}
	}

	tipoEvento := stringField(event, "tipo", "proyecto.unknown")
	data, _ := event["data"].(map[string]interface{})
	if data == nil {
		data = map[string]interface{}{}
	}

	proyectoIDStr := stringField(data, "proyecto_id", "")
	empresaIDStr := stringField(data, "empresa_id", "")

	if proyectoIDStr == "" || empresaIDStr == "" {
		log.Printf("[proyecto] missing proyecto_id/empresa_id in event %s — discarding", eventoID)
		_ = d.Ack(false)
		return
	}

	proyectoID, err := uuid.Parse(proyectoIDStr)
	if err != nil {
		log.Printf("[proyecto] invalid proyecto_id %q: %v — discarding", proyectoIDStr, err)
		_ = d.Ack(false)
		return
	}
	empresaID, err := uuid.Parse(empresaIDStr)
	if err != nil {
		log.Printf("[proyecto] invalid empresa_id %q: %v — discarding", empresaIDStr, err)
		_ = d.Ack(false)
		return
	}

	// Only act on proyecto.creado; ack others silently
	if tipoEvento != "proyecto.creado" && tipoEvento != "proyecto_creado" {
		log.Printf("[proyecto] unhandled subtype %q — acking", tipoEvento)
		_ = d.Ack(false)
		return
	}

	// ── 2. Transaction ───────────────────────────────────────────────────────
	tx, err := pool.Begin(ctx)
	if err != nil {
		log.Printf("[proyecto] pool.Begin: %v — requeuing", err)
		_ = d.Nack(false, true)
		return
	}
	defer func() {
		if err != nil {
			_ = tx.Rollback(ctx)
		}
	}()

	// ── 3. Idempotency ───────────────────────────────────────────────────────
	already, err := db.IsAlreadyProcessed(ctx, tx, eventoID)
	if err != nil {
		_ = tx.Rollback(ctx)
		_ = d.Nack(false, true)
		return
	}
	if already {
		log.Printf("[proyecto] duplicate event %s — skipping", eventoID)
		_ = tx.Rollback(ctx)
		_ = d.Ack(false)
		return
	}

	if _, err = db.InsertEventoEntrante(ctx, tx, eventoID, tipoEvento, d.Body); err != nil {
		_ = tx.Rollback(ctx)
		_ = d.Nack(false, true)
		return
	}

	// ── 4. Analisis ──────────────────────────────────────────────────────────
	nombre := fmt.Sprintf("Análisis proyecto creado – %s", proyectoIDStr)
	analisisID, err := db.InsertAnalisis(ctx, tx, proyectoID, empresaID, nombre, "COSTO")
	if err != nil {
		log.Printf("[proyecto] InsertAnalisis: %v", err)
		_ = tx.Rollback(ctx)
		_ = d.Nack(false, true)
		return
	}

	// ── 5. EjecucionAnalisis ─────────────────────────────────────────────────
	ejecucionID, err := db.InsertEjecucion(ctx, tx, analisisID)
	if err != nil {
		log.Printf("[proyecto] InsertEjecucion: %v", err)
		_ = tx.Rollback(ctx)
		_ = d.Nack(false, true)
		return
	}

	if err = db.MarkEventoProcessed(ctx, tx, eventoID); err != nil {
		_ = tx.Rollback(ctx)
		_ = d.Nack(false, true)
		return
	}

	if err = tx.Commit(ctx); err != nil {
		log.Printf("[proyecto] tx.Commit: %v", err)
		_ = d.Nack(false, true)
		return
	}

	duracionMs := time.Since(start).Milliseconds()

	// Post-commit: complete ejecucion
	resultado := map[string]interface{}{"status": "ok", "tipo": tipoEvento}
	if dbErr := db.CompleteEjecucion(ctx, pool, ejecucionID, duracionMs, resultado); dbErr != nil {
		log.Printf("[proyecto] CompleteEjecucion: %v (non-fatal)", dbErr)
	}

	log.Printf(
		"[proyecto] processed proyecto.creado: proyecto=%s analisis=%s ejecucion=%s duracion=%d ms",
		proyectoIDStr, analisisID, ejecucionID, duracionMs,
	)
	_ = d.Ack(false)
}
