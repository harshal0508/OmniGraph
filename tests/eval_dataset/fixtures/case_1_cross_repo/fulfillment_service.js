const db = require('knex')();
async function processFulfillment() {
    // Write to orders
    await db('orders').update({ status: 'SHIPPED' });
}
