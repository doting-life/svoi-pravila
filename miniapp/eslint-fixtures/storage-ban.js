/** Fixture intentionally using forbidden storage — must fail ESLint. */
export function touchStorage() {
    localStorage.setItem("x", "1");
    sessionStorage.setItem("y", "2");
    void indexedDB;
    document.cookie = "z=1";
}
