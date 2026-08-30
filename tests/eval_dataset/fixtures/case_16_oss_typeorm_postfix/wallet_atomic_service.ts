import { DataSource } from 'typeorm';
import { Wallet } from './entities/wallet.entity';

export class WalletAtomicService {
  constructor(private readonly dataSource: DataSource) {}

  async deductBalanceSafely(userId: string, currency: string, amount: number): Promise<void> {
    const queryRunner = this.dataSource.createQueryRunner();
    await queryRunner.connect();
    await queryRunner.startTransaction();

    try {
      // POST-FIX: Using pessimistic write lock via TypeORM object options
      const wallet = await queryRunner.manager.findOne(Wallet, {
        where: { userId, currency },
        lock: { mode: 'pessimistic_write' },
      });

      if (!wallet) throw new Error('Not found');
      if (wallet.balance < amount) throw new Error('Insufficient');

      wallet.balance -= amount;
      await queryRunner.manager.save(wallet);

      await queryRunner.commitTransaction();
    } catch (err) {
      await queryRunner.rollbackTransaction();
      throw err;
    } finally {
      await queryRunner.release();
    }
  }
}
