import {db} from '../index';
import { Role } from '../schema';
import { eq } from 'drizzle-orm';

async function seed() {
    // Clear existing data
    await db.delete(Role);

    // Seed Role
    await db.insert(Role).values([
        {
            description: 'Full Time',
        },
        {
            description: 'Part Time',
        },
        {
            description: 'Offshore',
        },
        {
            description: 'Offshore - EOX',
        },
        {
            description: 'Offshore - BGL',
        },
        {
            description: 'Management Team',
        },
        {
            description: 'Team Lead',
        }
    ]);
    console.log('Database seeded successfully!');
}

seed().catch((err) => {
    console.error('Error seeding database:', err);
    process.exit(1);
});