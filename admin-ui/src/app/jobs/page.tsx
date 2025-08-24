// UI section for Woo Job Queue Status
export default function Jobs() {
    return (
        <div className="p-6 max-w-4xl mx-auto">
            <h1 className="text-3xl font-bold mb-8 text-gray-900 leading-tight">Job Queue Status</h1>
            <div className="bg-white shadow-lg rounded-xl border border-gray-100 font-sans">
                <div className="overflow-x-auto">
                    <table className="min-w-full divide-y divide-gray-200 font-sans">
                        <thead className="bg-gray-50">
                            <tr>
                                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Job Type</th>
                                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Resource</th>
                                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Event</th>
                                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
                                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Created At</th>
                                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Actions</th>
                            </tr>
                        </thead>
                        <tbody className="bg-white divide-y divide-gray-200">
                            {/* Placeholder rows */}
                            <tr>
                                <td className="px-6 py-4 font-mono text-sm text-gray-900">woo.order.updated</td>
                                <td className="px-6 py-4 text-sm text-gray-900 font-sans">Order</td>
                                <td className="px-6 py-4 text-sm text-gray-900 font-sans">Updated</td>
                                <td className="px-6 py-4 text-green-600 font-sans">Done</td>
                                <td className="px-6 py-4 text-sm text-gray-900 font-sans">2025-08-24 12:35</td>
                                <td className="px-6 py-4">
                                    <button className="inline-flex items-center px-3 py-1 border border-blue-600 text-blue-600 text-xs font-medium rounded hover:bg-blue-50 transition">Retry</button>
                                    <button className="inline-flex items-center px-3 py-1 border border-gray-600 text-gray-600 text-xs font-medium rounded hover:bg-gray-50 transition ml-2">View</button>
                                </td>
                            </tr>
                            <tr>
                                <td className="px-6 py-4 font-mono text-sm text-gray-900">woo.customer.created</td>
                                <td className="px-6 py-4 text-sm text-gray-900 font-sans">Customer</td>
                                <td className="px-6 py-4 text-sm text-gray-900 font-sans">Created</td>
                                <td className="px-6 py-4 text-yellow-600 font-sans">Pending</td>
                                <td className="px-6 py-4 text-sm text-gray-900 font-sans">2025-08-24 12:33</td>
                                <td className="px-6 py-4">
                                    <button className="inline-flex items-center px-3 py-1 border border-blue-600 text-blue-600 text-xs font-medium rounded hover:bg-blue-50 transition">Retry</button>
                                    <button className="inline-flex items-center px-3 py-1 border border-gray-600 text-gray-600 text-xs font-medium rounded hover:bg-gray-50 transition ml-2">View</button>
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    );
}
