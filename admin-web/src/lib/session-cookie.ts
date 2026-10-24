// Shared by the proxy and server code. Production uses the __Host- prefix, which browsers accept only for a Secure,
// path=/ cookie with no Domain, so the session cookie can't be set or overwritten by any other subdomain.
export const SESSION_COOKIE = process.env.NODE_ENV === "production" ? "__Host-adm_session" : "adm_session";
