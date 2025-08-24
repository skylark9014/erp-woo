// UI section for Woo Archived Payloads (Inbox)
export default function Inbox() {
    return (
        <div className="p-6 max-w-4xl mx-auto">
            <h1 className="text-3xl font-bold mb-8 text-gray-900 leading-tight">Webhook Inbox (Archived Payloads)</h1>
            <div className="bg-white shadow-lg rounded-xl border border-gray-100 font-sans">
                <div className="overflow-x-auto">
                    <table className="min-w-full divide-y divide-gray-200 font-sans">
                        <thead className="bg-gray-50">
                            <tr>
                                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">File Name</th>
                                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Event Type</th>
                                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Received At</th>
                                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
                                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">View</th>
                                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Replay</th>
                            </tr>
                        </thead>
                        <tbody className="bg-white divide-y divide-gray-200">
                            {/* Placeholder rows */}
                            <tr>
                                <td className="px-6 py-4 font-mono text-sm text-gray-900">order-900001.order.created.json</td>
                                <td className="px-6 py-4 text-sm text-gray-900 font-sans">order.created</td>
                                <td className="px-6 py-4 text-sm text-gray-900 font-sans">2025-08-24 12:34</td>
                                <td className="px-6 py-4 text-green-600 font-sans">Archived</td>
                                <td className="px-6 py-4">
                                    <button className="inline-flex items-center px-3 py-1 border border-blue-600 text-blue-600 text-xs font-medium rounded hover:bg-blue-50 transition">View</button>
                                </td>
                                <td className="px-6 py-4">
                                    <button className="inline-flex items-center px-3 py-1 border border-indigo-600 text-indigo-600 text-xs font-medium rounded hover:bg-indigo-50 transition">Replay</button>
                                </td>
                            </tr>
                            <tr>
                                <td className="px-6 py-4 font-mono text-sm text-gray-900">order-900101.order.updated.json</td>
                                <td className="px-6 py-4 text-sm text-gray-900 font-sans">order.updated</td>
                                <td className="px-6 py-4 text-sm text-gray-900 font-sans">2025-08-24 12:32</td>
                                <td className="px-6 py-4 text-yellow-600 font-sans">Pending</td>
                                <td className="px-6 py-4">
                                    <button className="inline-flex items-center px-3 py-1 border border-blue-600 text-blue-600 text-xs font-medium rounded hover:bg-blue-50 transition">View</button>
                                </td>
                                <td className="px-6 py-4">
                                    <button className="inline-flex items-center px-3 py-1 border border-indigo-600 text-indigo-600 text-xs font-medium rounded hover:bg-indigo-50 transition">Replay</button>
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    );
}
