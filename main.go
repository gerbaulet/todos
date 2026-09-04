package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"
)

func healthcheck(args []string) int {
	flags := flag.NewFlagSet("healthcheck", flag.ContinueOnError)
	url := flags.String("url", "http://127.0.0.1:8765/healthz", "")
	if flags.Parse(args) != nil {
		return 2
	}
	client := http.Client{Timeout: 2 * time.Second}
	response, err := client.Get(*url)
	if err != nil {
		return 1
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return 1
	}
	return 0
}

func run(args []string) error {
	flags := flag.NewFlagSet("todo", flag.ContinueOnError)
	database := flags.String("database", filepath.Join("data", "todo.sqlite"), "SQLite-Datenbank")
	host := flags.String("host", "127.0.0.1", "Adresse")
	port := flags.Int("port", 8765, "Port")
	if err := flags.Parse(args); err != nil {
		return err
	}
	store, err := openDatabase(*database)
	if err != nil {
		return err
	}
	defer store.Close()
	listener, err := net.Listen("tcp", fmt.Sprintf("%s:%d", *host, *port))
	if err != nil {
		return err
	}
	httpServer := &http.Server{Handler: &server{db: store}, ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 30 * time.Second}
	stopped := make(chan os.Signal, 1)
	signal.Notify(stopped, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-stopped
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = httpServer.Shutdown(ctx)
	}()
	log.Printf("To-do-App läuft unter http://%s (Strg+C beendet)", listener.Addr())
	err = httpServer.Serve(listener)
	if err == http.ErrServerClosed {
		return nil
	}
	return err
}

func main() {
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		os.Exit(healthcheck(os.Args[2:]))
	}
	if err := run(os.Args[1:]); err != nil {
		log.Fatal(err)
	}
}
