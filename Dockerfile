FROM golang:1.26-alpine AS build

RUN apk add --no-cache gcc musl-dev
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY *.go ./
COPY static ./static
RUN CGO_ENABLED=1 go test ./...
RUN CGO_ENABLED=1 go build -trimpath -tags "netgo osusergo" \
    -ldflags '-s -w -linkmode external -extldflags "-static"' -o /todo .

FROM scratch
COPY --from=build /todo /todo
WORKDIR /app
EXPOSE 8765
ENTRYPOINT ["/todo"]
CMD ["--host", "0.0.0.0", "--port", "8765"]
