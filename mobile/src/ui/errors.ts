import { ApiError } from "@/core/api/client";

/** A message to show the customer. Backend messages are written to be safe to show; anything else is generic. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "Something went wrong. Please try again.";
}
