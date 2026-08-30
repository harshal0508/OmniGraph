const db = require('db');

async function processPayment(userId, amount) {
    // Read
    let balance = await db.query('SELECT balance FROM orders WHERE user_id = ?', [userId]);
    // Yields to event loop here, allowing another concurrent request to read before we write!
    
    // Write
    await db.query('UPDATE orders SET balance = balance - ?', [amount]);
}
