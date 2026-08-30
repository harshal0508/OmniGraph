import { DataSource } from 'typeorm';
import { Wallet } from './entities/wallet.entity';

export class WalletService {
  constructor(private readonly dataSource: DataSource) {}

  async deductBalanceSafely(userId: string, currency: string, amount: number): Promise<void> {
    const manager = this.dataSource.manager;
    
    // Pre-fix TOCTOU code: read balance
    const wallet = await manager.findOne(Wallet, {
      where: { userId, currency }
    });

    if (!wallet) {
      throw new Error('Wallet not found');
    }

    if (wallet.balance < amount) {
      throw new Error('Insufficient balance');
    }

    wallet.balance -= amount;

    // Pre-fix TOCTOU code: write balance
    await manager.save(wallet);
  }
}
