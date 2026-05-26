// Package consumer handles RabbitMQ connection, topology declaration, and
// message delivery to the goroutine worker pool.
package consumer

import (
	"fmt"
	"log"
	"os"
	"strconv"
	"time"

	amqp "github.com/rabbitmq/amqp091-go"
)

const (
	exchangeName = "bite_events"
	exchangeType = "topic"

	// Queue names
	queueEventos   = "bite.eventos"
	queueProyectos = "bite.proyectos"
	queueAnalisis  = "bite.analisis"
	queueReportes  = "bite.reportes"
)

// Queue binding keys (binding → queue)
var bindings = []struct {
	queue      string
	routingKey string
}{
	{queueEventos, "evento.#"},
	{queueProyectos, "proyecto.*"},
	{queueAnalisis, "analisis.*"},
	{queueReportes, "reporte.*"},
}

// Consumer holds an active AMQP connection + channel.
type Consumer struct {
	conn    *amqp.Connection
	channel *amqp.Channel
	url     string
}

// New creates a Consumer and establishes the AMQP connection.
// Retries up to 10 times with exponential back-off.
func New(url string) (*Consumer, error) {
	c := &Consumer{url: url}

	var err error
	for attempt := 1; attempt <= 10; attempt++ {
		err = c.connect()
		if err == nil {
			return c, nil
		}
		wait := time.Duration(attempt*2) * time.Second
		log.Printf("[rabbitmq] connection attempt %d failed: %v — retrying in %s", attempt, err, wait)
		time.Sleep(wait)
	}
	return nil, fmt.Errorf("rabbitmq: exhausted retries: %w", err)
}

// connect (re)opens conn + channel and declares topology.
func (c *Consumer) connect() error {
	conn, err := amqp.Dial(c.url)
	if err != nil {
		return fmt.Errorf("amqp.Dial: %w", err)
	}

	ch, err := conn.Channel()
	if err != nil {
		_ = conn.Close()
		return fmt.Errorf("conn.Channel: %w", err)
	}

	// Declare the durable topic exchange
	if err = ch.ExchangeDeclare(
		exchangeName,
		exchangeType,
		true,  // durable
		false, // auto-delete
		false, // internal
		false, // no-wait
		nil,
	); err != nil {
		return fmt.Errorf("ExchangeDeclare: %w", err)
	}

	// Declare queues + bindings
	for _, b := range bindings {
		if _, err = ch.QueueDeclare(b.queue, true, false, false, false, nil); err != nil {
			return fmt.Errorf("QueueDeclare(%s): %w", b.queue, err)
		}
		if err = ch.QueueBind(b.queue, b.routingKey, exchangeName, false, nil); err != nil {
			return fmt.Errorf("QueueBind(%s → %s): %w", b.routingKey, b.queue, err)
		}
	}

	// Set prefetch = WORKER_CONCURRENCY so each goroutine holds at most 1 message
	concurrency := workerConcurrency()
	if err = ch.Qos(concurrency, 0, false); err != nil {
		return fmt.Errorf("Qos: %w", err)
	}

	c.conn = conn
	c.channel = ch
	log.Printf("[rabbitmq] connected, prefetch=%d", concurrency)
	return nil
}

// Consume returns a delivery channel for the given queue.
// If the connection drops it reconnects and returns a fresh channel.
func (c *Consumer) Consume(queue string) (<-chan amqp.Delivery, error) {
	msgs, err := c.channel.Consume(
		queue,
		"",    // consumer tag (auto-generated)
		false, // auto-ack — worker acks manually after success
		false, // exclusive
		false, // no-local
		false, // no-wait
		nil,
	)
	if err != nil {
		return nil, fmt.Errorf("channel.Consume(%s): %w", queue, err)
	}
	return msgs, nil
}

// Close shuts down channel and connection gracefully.
func (c *Consumer) Close() {
	if c.channel != nil {
		_ = c.channel.Close()
	}
	if c.conn != nil {
		_ = c.conn.Close()
	}
	log.Println("[rabbitmq] connection closed")
}

// NotifyClose returns a channel that fires when the connection drops.
func (c *Consumer) NotifyClose() chan *amqp.Error {
	errCh := make(chan *amqp.Error, 1)
	c.conn.NotifyClose(errCh)
	return errCh
}

// workerConcurrency reads WORKER_CONCURRENCY from env (default 10).
func workerConcurrency() int {
	if v := os.Getenv("WORKER_CONCURRENCY"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			return n
		}
	}
	return 10
}
