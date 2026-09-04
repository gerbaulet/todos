FROM --platform=$BUILDPLATFORM golang:1.26-alpine AS build

ARG TARGETOS
ARG TARGETARCH
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY *.go ./
COPY static ./static
RUN CGO_ENABLED=0 go test ./...
RUN CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH \
    go build -trimpath -ldflags '-s -w' -o /todo .

FROM scratch
COPY --from=build /todo /todo
WORKDIR /app
EXPOSE 8765
ENTRYPOINT ["/todo"]
CMD ["--host", "0.0.0.0", "--port", "8765"]
