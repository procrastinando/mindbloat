import http.server
import socketserver
import functools

PORT = 8080
DIRECTORY = "sub"

# Create a handler class that will serve files from the specified DIRECTORY
Handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=DIRECTORY)

with socketserver.TCPServer(("", PORT), Handler) as httpd:
    print("=====================================================")
    print(f"  Simple Web Server started on port {PORT}")
    print(f"  Serving files from directory: '{DIRECTORY}'")
    print("=====================================================")
    
    # Start the server and keep it running forever (until you press Ctrl+C).
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        pass