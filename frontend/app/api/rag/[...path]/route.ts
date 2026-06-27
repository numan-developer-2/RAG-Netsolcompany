import { NextRequest } from "next/server";

const BACKEND_URL = process.env.RAG_API_URL ?? "http://127.0.0.1:8000";

type RouteContext = {
  params: Promise<{ path: string[] }>;
};

function backendEndpoint(path: string[]) {
  const cleanBase = BACKEND_URL.replace(/\/+$/, "");
  const cleanPath = path.map((part) => encodeURIComponent(part)).join("/");
  return `${cleanBase}/${cleanPath}`;
}

async function proxy(request: NextRequest, context: RouteContext) {
  const { path } = await context.params;
  const target = backendEndpoint(path);
  const headers = new Headers(request.headers);
  headers.delete("host");

  const response = await fetch(target, {
    method: request.method,
    headers,
    body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
    duplex: "half"
  } as RequestInit & { duplex: "half" });

  const responseHeaders = new Headers(response.headers);
  responseHeaders.delete("content-encoding");
  responseHeaders.delete("content-length");

  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: responseHeaders
  });
}

export async function GET(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}

export async function POST(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}
