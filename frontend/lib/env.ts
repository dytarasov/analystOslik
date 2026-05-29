// `??` (не `||`): при деплое за reverse-proxy под одним доменом ставят
// NEXT_PUBLIC_API_URL="" → запросы идут относительными путями на тот же origin.
// Пустая строка должна сохраниться, а не откатиться на localhost.
export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// Включает экран ввода UUID-ключа на клиентской части. В проде ставьте
// NEXT_PUBLIC_REQUIRE_ACCESS_KEY=1 (и T2R_ACCESS_KEY на бэке). В dev — выключено.
export const ACCESS_REQUIRED =
  process.env.NEXT_PUBLIC_REQUIRE_ACCESS_KEY === "1";
