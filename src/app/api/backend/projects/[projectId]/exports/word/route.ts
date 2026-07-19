const FORWARDED_RESPONSE_HEADERS = ["content-type", "content-disposition", "content-length"] as const;

function backendApiBaseUrl(): string {
  const configured = process.env.REVIA_API_BASE_URL ?? process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!configured) throw new Error("Backend API URL is not configured");
  return configured.replace(/\/$/, "");
}

export async function GET(
  request: Request,
  context: { params: Promise<{ projectId: string }> },
): Promise<Response> {
  try {
    const { projectId } = await context.params;
    const requestedUrl = new URL(request.url);
    const upstreamUrl = new URL(
      `${backendApiBaseUrl()}/projects/${encodeURIComponent(projectId)}/exports/word`,
    );
    upstreamUrl.search = requestedUrl.search;

    const requestHeaders = new Headers();
    for (const name of ["cookie", "authorization"] as const) {
      const value = request.headers.get(name);
      if (value) requestHeaders.set(name, value);
    }

    const upstream = await fetch(upstreamUrl, {
      method: "GET",
      headers: requestHeaders,
      cache: "no-store",
    });
    const responseHeaders = new Headers({ "Cache-Control": "no-store" });
    for (const name of FORWARDED_RESPONSE_HEADERS) {
      const value = upstream.headers.get(name);
      if (value) responseHeaders.set(name, value);
    }
    return new Response(upstream.body, {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch {
    return Response.json({ detail: "Word 导出服务暂时不可用" }, { status: 502 });
  }
}
