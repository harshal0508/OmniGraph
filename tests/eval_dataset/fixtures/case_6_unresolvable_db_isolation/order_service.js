async function handleOrder() {
    await db.query('UPDATE orders SET status = 1');
}
