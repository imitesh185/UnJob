const configuredApiUrl = process.env.NEXT_PUBLIC_API_URL?.trim();
export const API_URL = configuredApiUrl?.replace(/\/+$/, "") ?? "";

export class ApiError extends Error {
  constructor(message: string, public readonly status?: number) {
    super(message);
    this.name = "ApiError";
  }
}

export async function apiRequest<T>(
  path: string,
  options?: RequestInit,
  signal?: AbortSignal,
): Promise<T> {
  if (!API_URL) {
    throw new ApiError("NEXT_PUBLIC_API_URL is not configured.");
  }

  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...options,
      signal,
      headers: {
        Accept: "application/json",
        ...(options?.body ? { "Content-Type": "application/json" } : {}),
        ...options?.headers,
      },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("Could not reach the API. Check your connection and try again.");
  }

  if (!response.ok) {
    let detail = `Request failed (${response.status}).`;
    try {
      const body = (await response.json()) as { detail?: unknown; message?: unknown };
      const value = body.detail ?? body.message;
      if (typeof value === "string") detail = value;
      else if (Array.isArray(value)) {
        detail = value.map((item: unknown) => {
          if (typeof item === "string") return item;
          if (item && typeof item === "object" && "msg" in item) {
            const entry = item as { msg: unknown; loc?: unknown[] };
            return `${entry.loc?.filter((part) => part !== "body").join(".") || "Input"}: ${String(entry.msg)}`;
          }
          return "Invalid input.";
        }).join("; ") || detail;
      }
    } catch {
      // The fallback includes the HTTP status when the response is not JSON.
    }
    throw new ApiError(detail, response.status);
  }

  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
