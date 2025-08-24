// UI section for Woo Audit Log
export default function AuditLog() {
    return (
        <div className="p-6 max-w-4xl mx-auto">
            <h1 className="text-3xl font-bold mb-8 text-gray-900 leading-tight">Audit Log</h1>
            <div className="bg-white shadow-lg rounded-xl border border-gray-100 font-sans">
                <div className="overflow-x-auto">
                    <table className="min-w-full divide-y divide-gray-200 font-sans">
                        <thead className="bg-gray-50">
                            <tr>
                                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Action</th>
                                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">User</th>
                                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Timestamp</th>
                                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Details</th>
                            </tr>
                        </thead>
                        <tbody className="bg-white divide-y divide-gray-200">
                            {/* Placeholder rows */}
                            <tr>
                                <td className="px-6 py-4 text-sm text-gray-900 font-sans">Replay Payload</td>
                                <td className="px-6 py-4 text-sm text-gray-900 font-sans">admin</td>
                                <td className="px-6 py-4 text-sm text-gray-900 font-sans">2025-08-24 12:36</td>
                                <td className="px-6 py-4 text-sm text-gray-900 font-sans">order-900001.order.created.json replayed</td>
                            </tr>
                            <tr>
                                <td className="px-6 py-4 text-sm text-gray-900 font-sans">Backfill Orders</td>
                                <td className="px-6 py-4 text-sm text-gray-900 font-sans">admin</td>
                                <td className="px-6 py-4 text-sm text-gray-900 font-sans">2025-08-24 12:30</td>
                                <td className="px-6 py-4 text-sm text-gray-900 font-sans">Orders from 2025-08-01 to 2025-08-24</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    );
}
