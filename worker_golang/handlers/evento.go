// Package handlers contains message handlers for each queue.
// HandleEvento processes messages from bite.eventos (routing key evento.#).
package handlers

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"os"
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
		log.Printf("[ASR15][evento] bad JSON: %v — nacking without requeue", err)
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

	tipoEvento := stringField(event, "tipo", "unknown")
	data, _ := event["data"].(map[string]interface{})
	if data == nil {
		data = map[string]interface{}{}
	}

	log.Printf("[ASR15][evento] mensaje recibido: evento_id=%s tipo=%s routing_key=%s",
		eventoID, tipoEvento, d.RoutingKey)
	log.Printf("[ASR15][evento] inicio procesamiento: evento_id=%s", eventoID)

	proyectoIDStr := stringField(data, "proyecto_id", "")
	empresaIDStr := stringField(data, "empresa_id", "")

	if proyectoIDStr == "" || empresaIDStr == "" {
		log.Printf("[ASR15][evento] missing proyecto_id/empresa_id in event %s — discarding", eventoID)
		_ = d.Ack(false)
		return
	}

	proyectoID, err := uuid.Parse(proyectoIDStr)
	if err != nil {
		log.Printf("[ASR15][evento] invalid proyecto_id %q: %v — discarding", proyectoIDStr, err)
		_ = d.Ack(false)
		return
	}
	empresaID, err := uuid.Parse(empresaIDStr)
	if err != nil {
		log.Printf("[ASR15][evento] invalid empresa_id %q: %v — discarding", empresaIDStr, err)
		_ = d.Ack(false)
		return
	}

	// ── 2. Open transaction ─────────────────────────────────────────────────
	tx, err := pool.Begin(ctx)
	if err != nil {
		log.Printf("[ASR15][evento] pool.Begin: %v — requeuing", err)
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
		log.Printf("[ASR15][evento] idempotency check failed: %v", err)
		_ = tx.Rollback(ctx)
		_ = d.Nack(false, true)
		return
	}
	if already {
		log.Printf("[ASR15][evento] duplicate event %s — skipping", eventoID)
		_ = tx.Rollback(ctx)
		_ = d.Ack(false)
		return
	}

	if _, err = db.InsertEventoEntrante(ctx, tx, eventoID, tipoEvento, d.Body); err != nil {
		log.Printf("[ASR15][evento] InsertEventoEntrante: %v", err)
		_ = tx.Rollback(ctx)
		_ = d.Nack(false, true)
		return
	}

	// ── 4. Analisis ─────────────────────────────────────────────────────────
	nombre := fmt.Sprintf("Análisis batch – %s – %s", tipoEvento, proyectoIDStr)
	analisisID, err := db.InsertAnalisis(ctx, tx, proyectoID, empresaID, nombre, "COSTO")
	if err != nil {
		log.Printf("[ASR15][evento] InsertAnalisis: %v", err)
		_ = tx.Rollback(ctx)
		_ = d.Nack(false, true)
		return
	}

	// ── 5. EjecucionAnalisis ────────────────────────────────────────────────
	ejecucionID, err := db.InsertEjecucion(ctx, tx, analisisID)
	if err != nil {
		log.Printf("[ASR15][evento] InsertEjecucion: %v", err)
		_ = tx.Rollback(ctx)
		_ = d.Nack(false, true)
		return
	}

	// ASR15: simulate slow processing when SIMULATE_SLOW_PROCESSING=true
	if os.Getenv("SIMULATE_SLOW_PROCESSING") == "true" {
		log.Printf("[ASR15][evento] SIMULATE_SLOW_PROCESSING=true — sleeping 3s (evento_id=%s)", eventoID)
		time.Sleep(3 * time.Second)
	}

	// ── 6. Reporte ──────────────────────────────────────────────────────────
	hoy := time.Now().UTC()
	periodoInicio := time.Date(hoy.Year(), hoy.Month(), 1, 0, 0, 0, 0, time.UTC)
	periodoFin := hoy
	reporteNombre := fmt.Sprintf("Reporte batch – %s", tipoEvento)
	reporteTipo := reporteTipoForEvent(tipoEvento)
	reporteDatos := map[string]interface{}{
		"tipo_evento": tipoEvento,
		"data":        data,
	}
	reporteID, err := db.InsertReporte(
		ctx, tx, proyectoID, empresaID,
		reporteNombre, reporteTipo,
		periodoInicio, periodoFin, reporteDatos,
	)
	if err != nil {
		log.Printf("[ASR15][evento] InsertReporte: %v", err)
		_ = tx.Rollback(ctx)
		_ = d.Nack(false, true)
		return
	}

	// ── 7. Alerta ───────────────────────────────────────────────────────────
	alertaMensaje := fmt.Sprintf("Evento batch procesado: %s para proyecto %s", tipoEvento, proyectoIDStr)
	alertaID, err := db.InsertAlerta(ctx, tx, analisisID, reporteID, "ANOMALIA", alertaMensaje, "BAJA")
	if err != nil {
		log.Printf("[ASR15][evento] InsertAlerta: %v", err)
		_ = tx.Rollback(ctx)
		_ = d.Nack(false, true)
		return
	}

	if err = db.MarkEventoProcessed(ctx, tx, eventoID); err != nil {
		log.Printf("[ASR15][evento] MarkEventoProcessed: %v", err)
		_ = tx.Rollback(ctx)
		_ = d.Nack(false, true)
		return
	}

	if err = tx.Commit(ctx); err != nil {
		log.Printf("[ASR15][evento] tx.Commit: %v", err)
		_ = d.Nack(false, true)
		return
	}

	duracionMs := time.Since(start).Milliseconds()
	log.Printf("[ASR15][evento] commit exitoso: evento_id=%s duracion_total=%d ms", eventoID, duracionMs)

	// ── 8. Post-commit: update ejecucion duration ───────────────────────────
	resultado := map[string]interface{}{"status": "ok", "tipo": tipoEvento}
	if dbErr := db.CompleteEjecucion(ctx, pool, ejecucionID, duracionMs, resultado); dbErr != nil {
		log.Printf("[ASR15][evento] CompleteEjecucion: %v (non-fatal)", dbErr)
	}
	if dbErr := db.CompleteAnalisis(ctx, pool, analisisID); dbErr != nil {
		log.Printf("[ASR15][evento] CompleteAnalisis: %v (non-fatal)", dbErr)
	}

	// ── 9. Notificacion if slow ─────────────────────────────────────────────
	if duracionMs > 2000 {
		log.Printf("[ASR15][evento] análisis lento detectado: duracion=%d ms (umbral=2000 ms)", duracionMs)
		ejecucionRef := ejecucionID
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
		notifID, notifErr := db.InsertNotificacion(ctx, pool, notif)
		if notifErr != nil {
			log.Printf("[ASR15][evento] InsertNotificacion: %v (non-fatal)", notifErr)
		} else {
			log.Printf("[ASR15][evento] notificación creada: id=%s ejecucion=%s duracion=%d ms",
				notifID, ejecucionID, duracionMs)
		}
	}

	log.Printf(
		"[ASR15][evento] persisted: tipo=%s proyecto=%s analisis=%s reporte=%s alerta=%s duracion=%d ms",
		tipoEvento, proyectoIDStr, analisisID, reporteID, alertaID, duracionMs,
	)
	_ = d.Ack(false)
}

func reporteTipoForEvent(tipoEvento string) string {
	switch tipoEvento {
	case "reporte_solicitado":
		return "MENSUAL"
	default:
		return "PROYECTO"
	}
}

// stringField safely reads a string value from a map.
func stringField(m map[string]interface{}, key, fallback string) string {
	if v, ok := m[key].(string); ok && v != "" {
		return v
	}
	return fallback
}
