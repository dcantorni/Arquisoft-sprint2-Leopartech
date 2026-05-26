// Package handlers contains message handlers for each queue.
// HandleEvento processes messages from bite.eventos (routing key evento.#).
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
	"worker_golang/models"
)

// HandleEvento processes a single delivery from bite.eventos.
//
// Persistence order (architecture.md §5.4 + views.py procesar_evento_batch):
//  1. EventoEntrante  — idempotency + dedup
//  2. Analisis
//  3. EjecucionAnalisis
//  4. Reporte
//  5. Alerta
//  6. Notificacion — only if processing > 2 000 ms
func HandleEvento(ctx context.Context, pool *pgxpool.Pool, d amqp.Delivery) {
	start := time.Now()

	// ── 1. Parse payload ────────────────────────────────────────────────────
	var event map[string]interface{}
	if err := json.Unmarshal(d.Body, &event); err != nil {
		log.Printf("[evento] bad JSON: %v — nacking without requeue", err)
		_ = d.Nack(false, false)
		return
	}

	eventoID := d.MessageId
	if eventoID == "" {
		// Fallback: use message body field or generate
		if id, ok := event["evento_id"].(string); ok && id != "" {
			eventoID = id
		} else {
			eventoID = uuid.New().String()
		}
	}

	tipoEvento := stringField(event, "tipo", "unknown")
	data, _ := event["data"].(map[string]interface{})
	if data == nil {
		data = map[string]interface{}{}
	}

	proyectoIDStr := stringField(data, "proyecto_id", "")
	empresaIDStr := stringField(data, "empresa_id", "")

	if proyectoIDStr == "" || empresaIDStr == "" {
		log.Printf("[evento] missing proyecto_id/empresa_id in event %s — discarding", eventoID)
		_ = d.Ack(false)
		return
	}

	proyectoID, err := uuid.Parse(proyectoIDStr)
	if err != nil {
		log.Printf("[evento] invalid proyecto_id %q: %v — discarding", proyectoIDStr, err)
		_ = d.Ack(false)
		return
	}
	empresaID, err := uuid.Parse(empresaIDStr)
	if err != nil {
		log.Printf("[evento] invalid empresa_id %q: %v — discarding", empresaIDStr, err)
		_ = d.Ack(false)
		return
	}

	// ── 2. Open transaction ─────────────────────────────────────────────────
	tx, err := pool.Begin(ctx)
	if err != nil {
		log.Printf("[evento] pool.Begin: %v — requeuing", err)
		_ = d.Nack(false, true)
		return
	}
	defer func() {
		if err != nil {
			_ = tx.Rollback(ctx)
		}
	}()

	// ── 3. Idempotency check ────────────────────────────────────────────────
	already, err := db.IsAlreadyProcessed(ctx, tx, eventoID)
	if err != nil {
		log.Printf("[evento] idempotency check failed: %v", err)
		_ = tx.Rollback(ctx)
		_ = d.Nack(false, true)
		return
	}
	if already {
		log.Printf("[evento] duplicate event %s — skipping", eventoID)
		_ = tx.Rollback(ctx)
		_ = d.Ack(false)
		return
	}

	// Register receipt
	if _, err = db.InsertEventoEntrante(ctx, tx, eventoID, tipoEvento, d.Body); err != nil {
		log.Printf("[evento] InsertEventoEntrante: %v", err)
		_ = tx.Rollback(ctx)
		_ = d.Nack(false, true)
		return
	}

	// ── 4. Analisis ─────────────────────────────────────────────────────────
	nombre := fmt.Sprintf("Análisis batch – %s – %s", tipoEvento, proyectoIDStr)
	analisisID, err := db.InsertAnalisis(ctx, tx, proyectoID, empresaID, nombre, "COSTO")
	if err != nil {
		log.Printf("[evento] InsertAnalisis: %v", err)
		_ = tx.Rollback(ctx)
		_ = d.Nack(false, true)
		return
	}

	// ── 5. EjecucionAnalisis ────────────────────────────────────────────────
	ejecucionID, err := db.InsertEjecucion(ctx, tx, analisisID)
	if err != nil {
		log.Printf("[evento] InsertEjecucion: %v", err)
		_ = tx.Rollback(ctx)
		_ = d.Nack(false, true)
		return
	}

	// ── 6. Reporte ──────────────────────────────────────────────────────────
	hoy := time.Now().UTC()
	periodoInicio := time.Date(hoy.Year(), hoy.Month(), 1, 0, 0, 0, 0, time.UTC)
	periodoFin := hoy
	reporteDatos := map[string]interface{}{
		"tipo_evento": tipoEvento,
		"data":        data,
	}
	reporteID, err := db.InsertReporte(ctx, tx, proyectoID, empresaID, periodoInicio, periodoFin, reporteDatos)
	if err != nil {
		log.Printf("[evento] InsertReporte: %v", err)
		_ = tx.Rollback(ctx)
		_ = d.Nack(false, true)
		return
	}

	// ── 7. Alerta ───────────────────────────────────────────────────────────
	alertaMensaje := fmt.Sprintf("Evento batch procesado: %s para proyecto %s", tipoEvento, proyectoIDStr)
	alertaID, err := db.InsertAlerta(ctx, tx, analisisID, reporteID, "ANOMALIA", alertaMensaje, "BAJA")
	if err != nil {
		log.Printf("[evento] InsertAlerta: %v", err)
		_ = tx.Rollback(ctx)
		_ = d.Nack(false, true)
		return
	}

	// Mark as processed inside the same transaction
	if err = db.MarkEventoProcessed(ctx, tx, eventoID); err != nil {
		log.Printf("[evento] MarkEventoProcessed: %v", err)
		_ = tx.Rollback(ctx)
		_ = d.Nack(false, true)
		return
	}

	if err = tx.Commit(ctx); err != nil {
		log.Printf("[evento] tx.Commit: %v", err)
		_ = d.Nack(false, true)
		return
	}

	duracionMs := time.Since(start).Milliseconds()

	// ── 8. Post-commit: update ejecucion duration ───────────────────────────
	resultado := map[string]interface{}{"status": "ok", "tipo": tipoEvento}
	if dbErr := db.CompleteEjecucion(ctx, pool, ejecucionID, duracionMs, resultado); dbErr != nil {
		log.Printf("[evento] CompleteEjecucion: %v (non-fatal)", dbErr)
	}
	if dbErr := db.CompleteAnalisis(ctx, pool, analisisID); dbErr != nil {
		log.Printf("[evento] CompleteAnalisis: %v (non-fatal)", dbErr)
	}

	// ── 9. Notificacion if slow ─────────────────────────────────────────────
	if duracionMs > 2000 {
		ejecucionRef := ejecucionID // copy for pointer
		notif := models.Notificacion{
			EjecucionAnalisisID: &ejecucionRef,
			UsuarioID:           empresaID,
			EmailDestino:        "admin@bite.co",
			Tipo:                "EMAIL",
			Asunto:              fmt.Sprintf("Análisis completado (lento) – %s", nombre),
			Cuerpo: fmt.Sprintf(
				"El análisis \"%s\" completó en %d ms (umbral: 2000 ms). Reporte ID: %s",
				nombre, duracionMs, reporteID,
			),
		}
		if _, notifErr := db.InsertNotificacion(ctx, pool, notif); notifErr != nil {
			log.Printf("[evento] InsertNotificacion: %v (non-fatal)", notifErr)
		} else {
			log.Printf("[evento] notificacion emitida por análisis lento: %d ms", duracionMs)
		}
	}

	log.Printf(
		"[evento] persisted: tipo=%s proyecto=%s analisis=%s reporte=%s alerta=%s duracion=%d ms",
		tipoEvento, proyectoIDStr, analisisID, reporteID, alertaID, duracionMs,
	)
	_ = d.Ack(false)
}

// stringField safely reads a string value from a map.
func stringField(m map[string]interface{}, key, fallback string) string {
	if v, ok := m[key].(string); ok && v != "" {
		return v
	}
	return fallback
}
