// UI section for Woo Manual Backfill Actions
export default function Backfill() {
    return (
        <div className="p-6 max-w-4xl mx-auto">
            <h1 className="text-3xl font-extrabold mb-8 text-gray-800 tracking-tight">Manual Backfill Actions</h1>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                {/* Backfill Orders */}
                <div className="bg-white shadow-lg rounded-xl border border-gray-100 p-6 mb-6">
                    <h2 className="font-bold mb-4 text-lg text-blue-700">Backfill Orders</h2>
                    <form className="space-y-4">
                        <div>
                            <label className="block text-sm font-medium text-gray-700 mb-1">Date Range</label>
                            <div className="flex gap-2">
                                <input type="date" className="border rounded px-3 py-2 w-1/2" />
                                <input type="date" className="border rounded px-3 py-2 w-1/2" />
                            </div>
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-gray-700 mb-1">Status</label>
                            <select className="border rounded px-3 py-2 w-full">
                                <option>Any</option>
                                <option>Processing</option>
                                <option>Completed</option>
                            </select>
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-gray-700 mb-1">Enqueue Type</label>
                            <select className="border rounded px-3 py-2 w-full">
                                <option>Updated</option>
                                <option>Created</option>
                            </select>
                        </div>
                        <button className="w-full py-2 bg-blue-600 text-white font-semibold rounded hover:bg-blue-700 transition">Submit</button>
                    </form>
                </div>
                {/* Backfill Customers */}
                <div className="bg-white shadow-lg rounded-xl border border-gray-100 p-6 mb-6">
                    <h2 className="font-bold mb-4 text-lg text-blue-700">Backfill Customers</h2>
                    <form className="space-y-4">
                        <div>
                            <label className="block text-sm font-medium text-gray-700 mb-1">Date Range</label>
                            <div className="flex gap-2">
                                <input type="date" className="border rounded px-3 py-2 w-1/2" />
                                <input type="date" className="border rounded px-3 py-2 w-1/2" />
                            </div>
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-gray-700 mb-1">Enqueue Type</label>
                            <select className="border rounded px-3 py-2 w-full">
                                <option>Updated</option>
                                <option>Created</option>
                            </select>
                        </div>
                        <button className="w-full py-2 bg-blue-600 text-white font-semibold rounded hover:bg-blue-700 transition">Submit</button>
                    </form>
                </div>
                {/* Backfill Single Order */}
                <div className="bg-white shadow-lg rounded-xl border border-gray-100 p-6 mb-6">
                    <h2 className="font-bold mb-4 text-lg text-blue-700">Backfill Single Order</h2>
                    <form className="space-y-4">
                        <div>
                            <label className="block text-sm font-medium text-gray-700 mb-1">Order ID</label>
                            <input type="number" className="border rounded px-3 py-2 w-full" />
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-gray-700 mb-1">Enqueue Type</label>
                            <select className="border rounded px-3 py-2 w-full">
                                <option>Updated</option>
                                <option>Created</option>
                            </select>
                        </div>
                        <button className="w-full py-2 bg-blue-600 text-white font-semibold rounded hover:bg-blue-700 transition">Submit</button>
                    </form>
                </div>
                {/* Backfill Refunds */}
                <div className="bg-white shadow-lg rounded-xl border border-gray-100 p-6 mb-6">
                    <h2 className="font-bold mb-4 text-lg text-blue-700">Backfill Refunds</h2>
                    <form className="space-y-4">
                        <div>
                            <label className="block text-sm font-medium text-gray-700 mb-1">Date Range</label>
                            <div className="flex gap-2">
                                <input type="date" className="border rounded px-3 py-2 w-1/2" />
                                <input type="date" className="border rounded px-3 py-2 w-1/2" />
                            </div>
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-gray-700 mb-1">Status</label>
                            <select className="border rounded px-3 py-2 w-full">
                                <option>Any</option>
                                <option>Processing</option>
                                <option>Completed</option>
                            </select>
                        </div>
                        <button className="w-full py-2 bg-blue-600 text-white font-semibold rounded hover:bg-blue-700 transition">Submit</button>
                    </form>
                </div>
            </div>
        </div>
    );
}
