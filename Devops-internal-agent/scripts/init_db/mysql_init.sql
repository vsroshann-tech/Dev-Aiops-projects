-- Local Ubuntu POC MySQL initialization with strict read-only user
CREATE USER IF NOT EXISTS 'devops_ro'@'%' IDENTIFIED BY 'devops_ro_pass';

CREATE TABLE IF NOT EXISTS orders (
    id INT AUTO_INCREMENT PRIMARY KEY,
    order_ref VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO orders (order_ref, status) VALUES
    ('ORD-1001', 'completed'),
    ('ORD-1002', 'pending_payment'),
    ('ORD-1003', 'failed_timeout');

GRANT SELECT, SHOW VIEW, PROCESS ON orders_ro.* TO 'devops_ro'@'%';
GRANT PROCESS ON *.* TO 'devops_ro'@'%';
FLUSH PRIVILEGES;
