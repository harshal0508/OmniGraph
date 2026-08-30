class UserAPI:
    def process(self):
        # Writes to users
        db.execute("UPDATE users SET status='active'")
        # Calls its own validate
        self.validate()

    def validate(self):
        # Reads from users
        db.execute("SELECT * FROM users")
        return True


class ProductAPI:
    def process(self):
        # Writes to products
        db.execute("UPDATE products SET status='active'")
        # Calls its own validate
        self.validate()

    def validate(self):
        # Reads from products
        db.execute("SELECT * FROM products")
        return True
